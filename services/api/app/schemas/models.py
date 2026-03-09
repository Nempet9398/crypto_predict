from pydantic import BaseModel


class ModelInfo(BaseModel):
    id: str
    label: str
    available: bool


class ModelListResponse(BaseModel):
    models: list[ModelInfo]
