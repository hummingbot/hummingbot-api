"""System router models (FEAT-122)."""

from pydantic import BaseModel, Field


class SelfUpgradeRequest(BaseModel):
    """Consent to what a self-upgrade destroys.

    Restarting the API closes every RUNNING executor as SYSTEM_CLEANUP and they are not
    restored, so the one field here is the acknowledgement of that -- and it defaults to
    False, which means a caller that sends an empty body is refused rather than served.
    Bot containers are separate and keep running.
    """

    acknowledge_executor_loss: bool = Field(
        default=False,
        description=(
            "Confirms that every running executor will be closed as SYSTEM_CLEANUP and not "
            "restored. Required when the server has any running executor."
        ),
    )
