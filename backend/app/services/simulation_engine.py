import math
import asyncio
from enum import Enum
from typing import Callable


class CellStatus(str, Enum):
    COMBUSTIBLE = "combustible"
    FUEGO = "fuego"
    QUEMADO = "quemado"

    def __str__(self) -> str:
        return self.value


SendCallback = Callable[[list[dict]], None]


class SimulationEngine:
    GRID_SIZE = 40

    def __init__(self) -> None:
        self.grid: list[list[CellStatus]] = []
        self.rows: int = 0
        self.cols: int = 0
        self.running = False
        self.paused = False
        self._send_callback: SendCallback | None = None
        self._task: asyncio.Task | None = None
        self._zone_bounds: tuple[float, float, float, float] = (0, 0, 0, 0)

    def configure(
        self,
        wind_speed: float,
        wind_direction: float,
        ignition_lat: float,
        ignition_lng: float,
        zone_coords: list[list[float]],
        send_callback: SendCallback,
    ) -> None:
        self._compute_zone_bounds(zone_coords)
        self._build_grid()
        self.wind_speed = wind_speed
        self.wind_direction = wind_direction
        self._send_callback = send_callback

        row, col = self._geo_to_grid(ignition_lat, ignition_lng)
        self.grid[row][col] = CellStatus.FUEGO

    def _compute_zone_bounds(self, coords: list[list[float]]) -> None:
        lats = [p[0] for p in coords]
        lngs = [p[1] for p in coords]
        self._zone_bounds = (
            min(lats),
            max(lats),
            min(lngs),
            max(lngs),
        )
        lat_span = self._zone_bounds[1] - self._zone_bounds[0]
        lng_span = self._zone_bounds[3] - self._zone_bounds[2]
        if lat_span == 0:
            self._zone_bounds = (
                self._zone_bounds[0] - 0.001,
                self._zone_bounds[1] + 0.001,
                self._zone_bounds[2],
                self._zone_bounds[3],
            )
        if lng_span == 0:
            self._zone_bounds = (
                self._zone_bounds[0],
                self._zone_bounds[1],
                self._zone_bounds[2] - 0.001,
                self._zone_bounds[3] + 0.001,
            )
        self.rows = self.GRID_SIZE
        self.cols = self.GRID_SIZE

    def _build_grid(self) -> None:
        self.grid = [
            [CellStatus.COMBUSTIBLE for _ in range(self.cols)]
            for _ in range(self.rows)
        ]

    def _geo_to_grid(self, lat: float, lng: float) -> tuple[int, int]:
        lat_min, lat_max, lng_min, lng_max = self._zone_bounds
        lat_span = lat_max - lat_min
        lng_span = lng_max - lng_min
        row = int(((lat_max - lat) / lat_span) * self.rows)
        col = int(((lng - lng_min) / lng_span) * self.cols)
        row = max(0, min(row, self.rows - 1))
        col = max(0, min(col, self.cols - 1))
        return row, col

    def _grid_to_geo(self, row: int, col: int) -> tuple[float, float]:
        lat_min, lat_max, lng_min, lng_max = self._zone_bounds
        lat_step = (lat_max - lat_min) / self.rows
        lng_step = (lng_max - lng_min) / self.cols
        lat = lat_max - (row + 0.5) * lat_step
        lng = lng_min + (col + 0.5) * lng_step
        return lat, lng

    def start(self) -> None:
        self.running = True
        self.paused = False

    def pause(self) -> None:
        self.paused = True

    def stop(self) -> None:
        self.running = False
        self.paused = False

    async def run_loop(self) -> None:
        step = 0
        while self.running:
            if self.paused:
                await asyncio.sleep(0.5)
                continue

            await asyncio.sleep(0.8)

            updates = self._step()
            if self._send_callback:
                self._send_callback(updates)

            if not self._has_burning():
                self.running = False
                break

            step += 1

    def _step(self) -> list[dict]:
        new_grid = [row[:] for row in self.grid]
        updates: list[dict] = []

        for r in range(self.rows):
            for c in range(self.cols):
                if self.grid[r][c] == CellStatus.FUEGO:
                    new_grid[r][c] = CellStatus.QUEMADO
                    updates.append({"row": r, "col": c, "status": CellStatus.QUEMADO})
                    self._spread_to_neighbors(r, c, new_grid, updates)

        self.grid = new_grid
        return updates

    def _spread_to_neighbors(
        self,
        r: int,
        c: int,
        new_grid: list[list[CellStatus]],
        updates: list[dict],
    ) -> None:
        directions = [
            (-1, -1, 225),
            (-1, 0, 180),
            (-1, 1, 135),
            (0, -1, 270),
            (0, 1, 90),
            (1, -1, 315),
            (1, 0, 0),
            (1, 1, 45),
        ]

        for dr, dc, angle in directions:
            nr, nc = r + dr, c + dc
            if not (0 <= nr < self.rows and 0 <= nc < self.cols):
                continue
            if new_grid[nr][nc] != CellStatus.COMBUSTIBLE:
                continue

            prob = self._spread_probability(angle)
            if prob > 0 and (hash((nr, nc, id(self))) % 1000) / 1000 < prob:
                new_grid[nr][nc] = CellStatus.FUEGO
                updates.append({"row": nr, "col": nc, "status": CellStatus.FUEGO})

    def _spread_probability(self, neighbor_angle: float) -> float:
        base = 0.3
        if self.wind_speed <= 0:
            return base

        angle_diff = (neighbor_angle - self.wind_direction + 360) % 360
        if angle_diff > 180:
            angle_diff = 360 - angle_diff

        wind_factor = self.wind_speed / 30.0
        direction_bonus = math.cos(math.radians(angle_diff))
        direction_bonus = max(0, direction_bonus)

        return min(base + direction_bonus * wind_factor * 0.5, 0.95)

    def _has_burning(self) -> bool:
        return any(
            cell == CellStatus.FUEGO for row in self.grid for cell in row
        )

    @property
    def active(self) -> bool:
        return self.running and not self.paused
