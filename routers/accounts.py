from typing import Dict, List, Optional
from datetime import datetime

from fastapi import APIRouter, HTTPException, Depends, Query
from starlette import status

from services.accounts_service import AccountsService
from deps import get_accounts_service
from models import (
    PaginatedResponse,
    GatewayWalletCredential,
    GatewayWalletInfo,
    CreateWalletRequest,
    ShowPrivateKeyRequest,
    SendTransactionRequest,
    SetDefaultWalletRequest,
)

router = APIRouter(tags=["Accounts"], prefix="/accounts")


@router.get("/", response_model=List[str])
async def list_accounts(accounts_service: AccountsService = Depends(get_accounts_service)):
    """
    Get a list of all account names in the system.
    
    Returns:
        List of account names
    """
    return accounts_service.list_accounts()


@router.get("/{account_name}/credentials", response_model=List[str])
async def list_account_credentials(account_name: str,
                                   accounts_service: AccountsService = Depends(get_accounts_service)):
    """
    Get a list of all connectors that have credentials configured for a specific account.

    Args:
        account_name: Name of the account to list credentials for

    Returns:
        List of connector names that have credentials configured

    Raises:
        HTTPException: 404 if account not found
    """
    try:
        credentials = accounts_service.list_credentials(account_name)
        # Remove .yml extension from filenames
        return [cred.replace('.yml', '') for cred in credentials]
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/add-account", status_code=status.HTTP_201_CREATED)
async def add_account(account_name: str, accounts_service: AccountsService = Depends(get_accounts_service)):
    """
    Create a new account with default configuration files.
    
    Args:
        account_name: Name of the new account to create
        
    Returns:
        Success message when account is created
        
    Raises:
        HTTPException: 400 if account already exists
    """
    try:
        accounts_service.add_account(account_name)
        return {"message": "Account added successfully."}
    except FileExistsError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/delete-account")
async def delete_account(account_name: str, accounts_service: AccountsService = Depends(get_accounts_service)):
    """
    Delete an account and all its associated credentials.
    
    Args:
        account_name: Name of the account to delete
        
    Returns:
        Success message when account is deleted
        
    Raises:
        HTTPException: 400 if trying to delete master account, 404 if account not found
    """
    try:
        if account_name == "master_account":
            raise HTTPException(status_code=400, detail="Cannot delete master account.")
        await accounts_service.delete_account(account_name)
        return {"message": "Account deleted successfully."}
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/delete-credential/{account_name}/{connector_name}")
async def delete_credential(account_name: str, connector_name: str, accounts_service: AccountsService = Depends(get_accounts_service)):
    """
    Delete a specific connector credential for an account.
    
    Args:
        account_name: Name of the account
        connector_name: Name of the connector to delete credentials for
        
    Returns:
        Success message when credential is deleted
        
    Raises:
        HTTPException: 404 if credential not found
    """
    try:
        await accounts_service.delete_credentials(account_name, connector_name)
        return {"message": "Credential deleted successfully."}
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/add-credential/{account_name}/{connector_name}", status_code=status.HTTP_201_CREATED)
async def add_credential(account_name: str, connector_name: str, credentials: Dict, accounts_service: AccountsService = Depends(get_accounts_service)):
    """
    Add or update connector credentials (API keys) for a specific account and connector.

    Args:
        account_name: Name of the account
        connector_name: Name of the connector
        credentials: Dictionary containing the connector credentials

    Returns:
        Success message when credentials are added

    Raises:
        HTTPException: 400 if there's an error adding the credentials
    """
    try:
        await accounts_service.add_credentials(account_name, connector_name, credentials)
        return {"message": "Connector credentials added successfully."}
    except Exception as e:
        await accounts_service.delete_credentials(account_name, connector_name)
        raise HTTPException(status_code=400, detail=str(e))


# ============================================
# Gateway Wallet Management Endpoints
# ============================================

@router.get("/gateway/wallets")
async def list_gateway_wallets(accounts_service: AccountsService = Depends(get_accounts_service)):
    """
    List all wallets managed by Gateway.
    Gateway manages its own encrypted wallet storage.

    Returns:
        List of wallet information from Gateway

    Raises:
        HTTPException: 503 if Gateway unavailable
    """
    try:
        wallets = await accounts_service.get_gateway_wallets()
        return wallets
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/gateway/wallet/add", status_code=status.HTTP_201_CREATED)
async def add_gateway_wallet(
    wallet_credential: GatewayWalletCredential,
    accounts_service: AccountsService = Depends(get_accounts_service)
):
    """
    Add an existing wallet to Gateway using its private key.
    Gateway handles encryption and storage internally.

    Args:
        wallet_credential: Wallet credentials (chain, private_key, and optional set_default)

    Returns:
        Wallet information from Gateway including address

    Raises:
        HTTPException: 503 if Gateway unavailable, 400 on validation error
    """
    try:
        result = await accounts_service.add_gateway_wallet(
            chain=wallet_credential.chain,
            private_key=wallet_credential.private_key,
            set_default=wallet_credential.set_default
        )
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/gateway/wallet/{chain}/{address}")
async def remove_gateway_wallet(
    chain: str,
    address: str,
    accounts_service: AccountsService = Depends(get_accounts_service)
):
    """
    Remove a wallet from Gateway.

    Args:
        chain: Blockchain chain (e.g., 'solana', 'ethereum')
        address: Wallet address to remove

    Returns:
        Success message

    Raises:
        HTTPException: 503 if Gateway unavailable
    """
    try:
        result = await accounts_service.remove_gateway_wallet(chain, address)
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/gateway/wallet/create", status_code=status.HTTP_201_CREATED)
async def create_gateway_wallet(
    request: CreateWalletRequest,
    accounts_service: AccountsService = Depends(get_accounts_service)
) -> Dict:
    """
    Create a new wallet in Gateway.

    Args:
        request: Contains chain and set_default flag

    Returns:
        Dict with address and chain of the created wallet.

    Example: POST /accounts/gateway/wallet/create
    {
        "chain": "solana",
        "set_default": true
    }
    """
    try:
        if not await accounts_service.gateway_client.ping():
            raise HTTPException(status_code=503, detail="Gateway service is not available")

        result = await accounts_service.gateway_client.create_wallet(
            chain=request.chain,
            set_default=request.set_default
        )

        if result is None:
            raise HTTPException(status_code=502, detail="Failed to create wallet: Gateway returned no response")

        if "error" in result:
            raise HTTPException(status_code=400, detail=f"Failed to create wallet: {result.get('error')}")

        return result

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error creating wallet: {str(e)}")


@router.post("/gateway/wallet/show-private-key")
async def show_gateway_wallet_private_key(
    request: ShowPrivateKeyRequest,
    accounts_service: AccountsService = Depends(get_accounts_service)
) -> Dict:
    """
    Show private key for a wallet.

    WARNING: This endpoint exposes sensitive information. Use with caution.

    Args:
        request: Contains chain, address, and passphrase

    Returns:
        Dict with privateKey field.

    Example: POST /accounts/gateway/wallet/show-private-key
    {
        "chain": "solana",
        "address": "<wallet-address>",
        "passphrase": "<gateway-passphrase>"
    }
    """
    try:
        if not await accounts_service.gateway_client.ping():
            raise HTTPException(status_code=503, detail="Gateway service is not available")

        result = await accounts_service.gateway_client.show_private_key(
            chain=request.chain,
            address=request.address,
            passphrase=request.passphrase
        )

        if result is None:
            raise HTTPException(status_code=502, detail="Failed to retrieve private key: Gateway returned no response")

        if "error" in result:
            raise HTTPException(status_code=400, detail=f"Failed to retrieve private key: {result.get('error')}")

        return result

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error retrieving private key: {str(e)}")


@router.post("/gateway/wallet/send")
async def send_gateway_wallet_transaction(
    request: SendTransactionRequest,
    accounts_service: AccountsService = Depends(get_accounts_service)
) -> Dict:
    """
    Send a native token transaction.

    Args:
        request: Contains chain, network, sender address, recipient address, and amount

    Returns:
        Dict with transaction signature/hash.

    Example: POST /accounts/gateway/wallet/send
    {
        "chain": "solana",
        "network": "mainnet-beta",
        "address": "<sender-address>",
        "to_address": "<recipient-address>",
        "amount": "0.001"
    }
    """
    try:
        if not await accounts_service.gateway_client.ping():
            raise HTTPException(status_code=503, detail="Gateway service is not available")

        result = await accounts_service.gateway_client.send_transaction(
            chain=request.chain,
            network=request.network,
            address=request.address,
            to_address=request.to_address,
            amount=request.amount
        )

        if result is None:
            raise HTTPException(status_code=502, detail="Failed to send transaction: Gateway returned no response")

        if "error" in result:
            raise HTTPException(status_code=400, detail=f"Failed to send transaction: {result.get('error')}")

        return result

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error sending transaction: {str(e)}")


@router.post("/gateway/wallet/set-default")
async def set_default_gateway_wallet(
    request: SetDefaultWalletRequest,
    accounts_service: AccountsService = Depends(get_accounts_service)
) -> Dict:
    """
    Set the default wallet for a chain in Gateway.

    When multiple wallets are configured for a chain, this endpoint allows
    switching which wallet is used as the default for operations.

    Args:
        request: Contains chain and wallet address to set as default

    Returns:
        Dict with success status and updated wallet info.

    Example: POST /accounts/gateway/wallet/set-default
    {
        "chain": "solana",
        "address": "82SggYRE2Vo4jN4a2pk3aQ4SET4ctafZJGbowmCqyHx5"
    }
    """
    try:
        if not await accounts_service.gateway_client.ping():
            raise HTTPException(status_code=503, detail="Gateway service is not available")

        result = await accounts_service.gateway_client.set_default_wallet(
            chain=request.chain,
            address=request.address
        )

        if result is None:
            raise HTTPException(status_code=502, detail="Failed to set default wallet: Gateway returned no response")

        if "error" in result:
            raise HTTPException(status_code=400, detail=f"Failed to set default wallet: {result.get('error')}")

        return {
            "success": True,
            "message": f"Set {request.address} as default wallet for {request.chain}",
            "chain": request.chain,
            "address": request.address
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error setting default wallet: {str(e)}")
