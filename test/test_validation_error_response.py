"""A validator that raises ValueError must still produce the 422 JSON body, not a 500.

The dashboard auto-generates controller config names with a version suffix ('falcon_0.1');
`V2ControllerDeployment` rejects the '.', and the rejection used to come back as a 500 with a
text/plain body because the handler serialised pydantic's error list raw, ValueError object and
all (dashboard#281). The message the validator wrote is what the client needs to see.

Run with: pytest test/test_validation_error_response.py -v
"""
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient

from models import V2ControllerDeployment
from utils.validation_errors import validation_exception_handler


def _client() -> TestClient:
    app = FastAPI()
    app.add_exception_handler(RequestValidationError, validation_exception_handler)

    @app.post("/deploy")
    async def deploy(deployment: V2ControllerDeployment):
        return {"instance_name": deployment.instance_name}

    return TestClient(app, raise_server_exceptions=False)


def test_a_rejected_config_name_is_a_422_json_body_with_the_validators_message():
    response = _client().post("/deploy", json={
        "instance_name": "bot-1",
        "credentials_profile": "master_account",
        "controllers_config": ["experiment-falcon_0.1"],
    })

    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/json")
    detail = response.json()["detail"]
    assert detail[0]["loc"] == ["body", "controllers_config"]
    assert "Only letters, numbers, underscores and hyphens are allowed" in detail[0]["msg"]
    assert "experiment-falcon_0.1" in detail[0]["msg"]


def test_a_well_formed_deployment_is_not_touched_by_the_handler():
    response = _client().post("/deploy", json={
        "instance_name": "bot-1",
        "credentials_profile": "master_account",
        "controllers_config": ["experiment-falcon_0-1"],
    })

    assert response.status_code == 200
    assert response.json() == {"instance_name": "bot-1"}
