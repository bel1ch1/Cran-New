from pydantic import BaseModel, Field


class ZMarkerSettings(BaseModel):
    marker_size: int = Field(ge=1, le=1000)
    marker_id: int = Field(ge=1, le=1000)


class XYMarkerSettings(BaseModel):
    marker_size: int = Field(ge=1, le=1000)


class ApiMessage(BaseModel):
    message: str


class CommandResponse(BaseModel):
    message: str
    command: str

