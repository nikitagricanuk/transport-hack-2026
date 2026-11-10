"""Bounded public inputs for read-only network analysis."""
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class AnalysisPoint(BaseModel):
    lat: float | None = Field(default=None, ge=-90, le=90, allow_inf_nan=False)
    lon: float | None = Field(default=None, ge=-180, le=180, allow_inf_nan=False)
    node_id: int | None = None
    way_id: int | None = None

    @model_validator(mode="after")
    def one_kind(self):
        if sum((self.lat is not None or self.lon is not None, self.node_id is not None, self.way_id is not None)) != 1:
            raise ValueError("нужны координаты, node_id или way_id")
        if (self.lat is None) != (self.lon is None):
            raise ValueError("нужны обе координаты")
        return self


class CommonRequest(BaseModel):
    mode: Literal["car", "walk"] = "car"
    walk_kmh: float = Field(default=4.5, ge=1, le=12, allow_inf_nan=False)
    use_restrictions: bool = True
    use_closures: bool = False
    compare: Literal["original"] | None = None


class RouteRequest(CommonRequest):
    start: AnalysisPoint = Field(alias="from")
    to: AnalysisPoint
    metric: Literal["time", "length"] = "time"


class IsochroneRequest(CommonRequest):
    start: AnalysisPoint = Field(alias="from")
    step_min: float = Field(default=1, ge=0.1, le=10, allow_inf_nan=False)
    count: int = Field(default=5, ge=1, le=10)
    how: Literal["buffer", "hull"] = "buffer"
    buffer_m: float = Field(default=30, ge=1, le=100, allow_inf_nan=False)
    speed_source: Literal["free", "loaded"] = "free"

    @model_validator(mode="after")
    def budget(self):
        if self.count*self.step_min > 60:
            raise ValueError("не более 60 минут")
        return self


class CatchmentRequest(CommonRequest):
    mode: Literal["walk"] = "walk"
    set_a: list[int] = Field(default_factory=list, max_length=200)
    set_b: list[int] = Field(default_factory=list, max_length=200)
    limit_m: float = Field(default=500, ge=1, le=5000, allow_inf_nan=False)
    limit_min: float | None = Field(default=None, gt=0, le=60, allow_inf_nan=False)
    buffer_m: float = Field(default=40, ge=1, le=100, allow_inf_nan=False)


class PtRequest(CommonRequest):
    mode: Literal["walk"] = "walk"
    start: AnalysisPoint = Field(alias="from")
    bands_min: list[float] = Field(default=[5,10,15], min_length=1, max_length=6)
    pt_kmh: dict[str,float] = Field(default_factory=lambda:dict(bus=20,trolleybus=20,tram=20,minibus=20,share_taxi=20))
    wait_factor: float = Field(default=.5, ge=0, le=1, allow_inf_nan=False)
    max_transfers: int = Field(default=2, ge=0, le=3)
    headway_overrides: dict[int,float] = Field(default_factory=dict)
    headway_sources: dict[int,Literal["manual","mock"]] = Field(default_factory=dict)
    how: Literal["buffer","hull"] = "hull"
    buffer_m: float = Field(default=40, ge=1, le=100, allow_inf_nan=False)

    @model_validator(mode="after")
    def bounded_values(self):
        import math
        if any(not math.isfinite(v) or not 0 < v <= 60 for v in self.bands_min):
            raise ValueError("интервалы изохрон: 0–60 минут")
        if any(k not in {"bus","trolleybus","tram","minibus","share_taxi"} or not math.isfinite(v) or not 1 <= v <= 100 for k,v in self.pt_kmh.items()):
            raise ValueError("скорость ОТ: 1–100 км/ч")
        if len(self.headway_overrides)>1000 or any(not math.isfinite(v) or not .1 <= v <= 180 for v in self.headway_overrides.values()):
            raise ValueError("интервал: 0,1–180 минут; не более 1000 маршрутов")
        self.bands_min = sorted(set(self.bands_min))
        return self
