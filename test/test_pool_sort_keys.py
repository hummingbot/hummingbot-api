"""Pool discovery must rank by depth, and must reject a key the DEX will not take (GW-46).

Two defects, both found on UMBRA-USDC/meteora on 2026-08-20.

**The default was `volume`.** On a token whose DLMM pools are all idle every row ties at
volume_24h = 0.00, so the order is arbitrary and liquidity is never consulted. Of 73 pools:

    pool          TVL       rank under volume   rank under tvl
    3WLPDnHp...   15.34K    68 of 73            1
    HHHKtpPp...    1.07     47 of 73            5

Reading top-down, an agent picked the pool holding $1.07 over one holding four orders of
magnitude more, and reported the deep one as "not found" — it was at row 68. Volume ranks
pools by how much OTHERS traded; the LP question is how much depth is there. It is also
the field most likely to be uniformly zero, and a sort key that collapses to noise is
worse than one that merely ranks differently than you wanted.

**The documented keys did not all work.** The tool advertised "volume, tvl, feetvlratio,
etc.". Probed against the live upstreams:

    meteora (dlmm.datapi.meteora.ag)  tvl OK   volume_24h OK   fee_tvl_ratio_24h OK
                                      fees_24h 400   apr 400   liquidity 400   volume 400
    orca                              tvl, volume, fees, rewards, yieldovertvl OK
                                      liquidity 400

So `feetvlratio` was real but under another name, and hapi's own `_24h` suffixing turned
`fees` into `fees_24h`, which Meteora rejects outright. Both surfaced as an opaque hapi
500, which reads as a server fault rather than a wrong field name.
"""
import inspect

import pytest
from fastapi import HTTPException

from routers.gateway_clmm import _METEORA_SORT_KEYS, _ORCA_SORT_KEYS, _sort_field, get_clmm_pools


class TestTheDefault:
    def test_pools_are_ranked_by_depth_not_by_what_others_traded(self):
        default = inspect.signature(get_clmm_pools).parameters["sort_key"].default

        assert default.default == "tvl", (
            "ranking by volume buries the deepest pool whenever a pair is quiet"
        )


class TestMeteora:
    def test_volume_asks_the_upstream_for_the_field_it_actually_has(self):
        """Bare `volume` is a 400 from Meteora; the field is volume_24h."""
        assert _sort_field("meteora", "volume") == "volume_24h"

    def test_tvl_passes_through(self):
        assert _sort_field("meteora", "tvl") == "tvl"

    def test_the_documented_ratio_key_now_resolves_to_a_real_field(self):
        """feetvlratio was advertised and 400'd. The field exists under another name."""
        assert _sort_field("meteora", "feetvlratio") == "fee_tvl_ratio_24h"

    def test_fees_is_refused_here_rather_than_400ing_upstream(self):
        """Meteora has no fees sort, and hapi's own _24h suffixing made it `fees_24h`,
        which the API rejects. That arrived as an opaque 500."""
        with pytest.raises(HTTPException) as raised:
            _sort_field("meteora", "fees")

        assert raised.value.status_code == 400
        assert "fees" in raised.value.detail

    @pytest.mark.parametrize("key", ["apr", "liquidity", "lm_apr", "fees_24h", "nonsense"])
    def test_every_other_key_the_upstream_rejects_is_refused_here(self, key):
        with pytest.raises(HTTPException) as raised:
            _sort_field("meteora", key)

        assert raised.value.status_code == 400

    def test_the_refusal_names_the_keys_that_do_work(self):
        """A 400 that does not say what IS legal just moves the guessing."""
        with pytest.raises(HTTPException) as raised:
            _sort_field("meteora", "liquidity")

        for legal in _METEORA_SORT_KEYS:
            assert legal in raised.value.detail
        assert "meteora" in raised.value.detail


class TestOrca:
    @pytest.mark.parametrize("key", sorted(_ORCA_SORT_KEYS))
    def test_every_key_orca_accepts_passes_through_unchanged(self, key):
        """Orca takes the field alone, with direction as a separate parameter."""
        assert _sort_field("orca", key) == key

    def test_liquidity_is_refused(self):
        """Probed: orca 400s on it, same as meteora."""
        with pytest.raises(HTTPException) as raised:
            _sort_field("orca", "liquidity")

        assert raised.value.status_code == 400

    def test_a_meteora_only_key_is_not_silently_accepted(self):
        """feetvlratio is real on meteora and not on orca; the sets are per connector."""
        with pytest.raises(HTTPException):
            _sort_field("orca", "feetvlratio")


class TestNoSortRequested:
    def test_no_key_means_no_sort_parameter_rather_than_an_error(self):
        assert _sort_field("meteora", None) is None
        assert _sort_field("orca", "") is None


def test_the_two_connectors_do_not_share_one_list():
    """They genuinely differ — orca has fees and rewards, meteora has feetvlratio — and
    merging them would re-create the failure this fixes, in the other direction."""
    assert set(_METEORA_SORT_KEYS) != _ORCA_SORT_KEYS
