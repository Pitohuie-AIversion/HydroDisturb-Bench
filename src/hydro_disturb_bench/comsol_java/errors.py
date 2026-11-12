"""Shared COMSOL error codes and typed exceptions."""
from __future__ import annotations

# Error code constants matching project contract
ERR_MODEL_NOT_FOUND = "MODEL_NOT_FOUND"
ERR_API_UNSUPPORTED = "API_UNSUPPORTED"
ERR_NODE_NOT_FOUND = "NODE_NOT_FOUND"
ERR_INVALID_PARAMETER = "INVALID_PARAMETER"
ERR_INVALID_UNIT = "INVALID_UNIT"
ERR_COMSOL_CONNECTION_FAILED = "COMSOL_CONNECTION_FAILED"
ERR_MODEL_VALIDATION_FAILED = "MODEL_VALIDATION_FAILED"
ERR_SOLVER_FAILED = "SOLVER_FAILED"


class ComsolError(Exception):
    """Base exception for COMSOL bridge operations."""

    def __init__(self, error_code: str, message: str, details: str = "") -> None:
        super().__init__(f"[{error_code}] {message}")
        self.error_code = error_code
        self.message = message
        self.details = details


class ModelNotFoundError(ComsolError):
    def __init__(self, message: str, details: str = "") -> None:
        super().__init__(ERR_MODEL_NOT_FOUND, message, details)


class ApiUnsupportedError(ComsolError):
    def __init__(self, message: str, details: str = "") -> None:
        super().__init__(ERR_API_UNSUPPORTED, message, details)


class NodeNotFoundError(ComsolError):
    def __init__(self, message: str, details: str = "") -> None:
        super().__init__(ERR_NODE_NOT_FOUND, message, details)


class InvalidParameterError(ComsolError):
    def __init__(self, message: str, details: str = "") -> None:
        super().__init__(ERR_INVALID_PARAMETER, message, details)


class InvalidUnitError(ComsolError):
    def __init__(self, message: str, details: str = "") -> None:
        super().__init__(ERR_INVALID_UNIT, message, details)


class ComsolConnectionError(ComsolError):
    def __init__(self, message: str, details: str = "") -> None:
        super().__init__(ERR_COMSOL_CONNECTION_FAILED, message, details)


class ModelValidationError(ComsolError):
    def __init__(self, message: str, details: str = "") -> None:
        super().__init__(ERR_MODEL_VALIDATION_FAILED, message, details)


class SolverFailedError(ComsolError):
    def __init__(self, message: str, details: str = "") -> None:
        super().__init__(ERR_SOLVER_FAILED, message, details)


def map_error(error_code: str, message: str, details: str = "") -> ComsolError:
    """Map error code string to typed exception."""
    mapping = {
        ERR_MODEL_NOT_FOUND: ModelNotFoundError,
        ERR_API_UNSUPPORTED: ApiUnsupportedError,
        ERR_NODE_NOT_FOUND: NodeNotFoundError,
        ERR_INVALID_PARAMETER: InvalidParameterError,
        ERR_INVALID_UNIT: InvalidUnitError,
        ERR_COMSOL_CONNECTION_FAILED: ComsolConnectionError,
        ERR_MODEL_VALIDATION_FAILED: ModelValidationError,
        ERR_SOLVER_FAILED: SolverFailedError,
    }
    cls = mapping.get(error_code, ComsolError)
    if cls is ComsolError:
        return ComsolError(error_code, message, details)
    return cls(message, details)
