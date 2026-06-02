import math
import asyncio
import os
import json
import io
import subprocess
import random
import logging
from enum import Enum
from typing import Callable

import requests
import rasterio
from PIL import Image

logger = logging.getLogger(__name__)

OPENTOPOGRAPHY_API_KEY = "5090635d399f89371c7647df4ff02716"
WINDNINJA_CLI_PATH = "C:\\WindNinja\\WindNinja-3.12.0\\bin\\WindNinja_cli.exe"

DEM_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data")
DEM_PATH = os.path.join(DEM_DIR, "elevacion.tif")
BOUNDS_PATH = os.path.join(DEM_DIR, "elevacion_bounds.json")
GREENNESS_PATH = os.path.join(DEM_DIR, "vegetacion.json")

DEFAULT_HUMIDITY = 0.4
DEFAULT_ELEVATION = 1000.0


class CellStatus(str, Enum):
    COMBUSTIBLE = "combustible"
    FUEGO = "fuego"
    QUEMADO = "quemado"

    def __str__(self) -> str:
        return self.value


SendCallback = Callable[[list[dict]], None]


class SimulationEngine:
    GRID_SIZE = 100

    def __init__(self) -> None:
        self.grid: list[list[CellStatus]] = []
        self.rows: int = 0
        self.cols: int = 0
        self.running = False
        self.paused = False
        self._send_callback: SendCallback | None = None
        self._task: asyncio.Task | None = None
        self._zone_bounds: tuple[float, float, float, float] = (0, 0, 0, 0)
        self.elevation_grid: list[list[float]] | None = None
        self.wind_speed: float = 0
        self.wind_direction: float = 0
        self._humidity: float = DEFAULT_HUMIDITY
        self.wind_speed_grid: list[list[float]] | None = None
        self.wind_direction_grid: list[list[float]] | None = None
        self.greenness_grid: list[list[float]] | None = None
        self.burn_remaining: list[list[int]] | None = None

    def configure(
        self,
        wind_speed: float,
        wind_direction: float,
        ignition_lat: float,
        ignition_lng: float,
        zone_coords: list[list[float]],
        send_callback: SendCallback,
        humidity: float = DEFAULT_HUMIDITY,
    ) -> None:
        self._compute_zone_bounds(zone_coords)
        self._download_elevation_dem()
        self._load_real_elevation_dem()
        self._compute_vegetation_greenness()
        self.wind_speed = wind_speed
        self.wind_direction = wind_direction
        self._humidity = humidity
        self._run_windninja()
        self._build_grid()
        self._send_callback = send_callback

        row, col = self._geo_to_grid(ignition_lat, ignition_lng)
        self.grid[row][col] = CellStatus.FUEGO
        if self.burn_remaining is not None:
            self.burn_remaining[row][col] = random.randint(3, 5)

    # ------------------------------------------------------------------
    # Elevation DEM download & loading
    # ------------------------------------------------------------------

    def _download_elevation_dem(self) -> None:
        if os.path.exists(DEM_PATH) and os.path.exists(BOUNDS_PATH):
            try:
                with open(BOUNDS_PATH, "r") as f:
                    saved = json.load(f)
                saved_bounds = (
                    saved["lat_min"],
                    saved["lat_max"],
                    saved["lng_min"],
                    saved["lng_max"],
                )
                if all(
                    abs(a - b) < 1e-5
                    for a, b in zip(saved_bounds, self._zone_bounds)
                ):
                    logger.info("DEM ya descargado y bounds coinciden, omitiendo descarga")
                    return
                else:
                    logger.info("Bounds cambiaron, redescargando DEM...")
            except Exception:
                logger.warning("Error leyendo bounds JSON, redescargando...")
        else:
            logger.info("DEM no encontrado, descargando...")

        os.makedirs(DEM_DIR, exist_ok=True)

        lat_min, lat_max, lng_min, lng_max = self._zone_bounds
        params = {
            "demtype": "SRTMGL1",
            "south": lat_min,
            "north": lat_max,
            "west": lng_min,
            "east": lng_max,
            "outputFormat": "GTiff",
            "apikey": OPENTOPOGRAPHY_API_KEY,
        }

        try:
            resp = requests.get(
                "https://portal.opentopography.org/API/globaldem",
                params=params,
                stream=True,
                timeout=60,
            )
            if resp.status_code != 200:
                logger.error(
                    "Error al descargar DEM: HTTP %s - %s",
                    resp.status_code,
                    resp.text[:200],
                )
                return

            with open(DEM_PATH, "wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    f.write(chunk)

            with open(BOUNDS_PATH, "w") as f:
                json.dump(
                    {
                        "lat_min": lat_min,
                        "lat_max": lat_max,
                        "lng_min": lng_min,
                        "lng_max": lng_max,
                    },
                    f,
                )

            logger.info("DEM descargado exitosamente en %s", DEM_PATH)
        except requests.RequestException as e:
            logger.error("Error de conexión al descargar DEM: %s", e)

    def _load_real_elevation_dem(self) -> None:
        self.elevation_grid = [
            [0.0 for _ in range(self.cols)] for _ in range(self.rows)
        ]

        if not os.path.exists(DEM_PATH):
            logger.info("Archivo DEM no encontrado, usando elevación plana")
            return

        try:
            with rasterio.open(DEM_PATH) as dataset:
                band = dataset.read(1)
                nodata = dataset.nodata

                for r in range(self.rows):
                    for c in range(self.cols):
                        lat, lng = self._grid_to_geo(r, c)
                        try:
                            raster_row, raster_col = dataset.index(lng, lat)
                            if 0 <= raster_row < band.shape[0] and 0 <= raster_col < band.shape[1]:
                                value = float(band[raster_row, raster_col])
                                if nodata is None or value != nodata:
                                    self.elevation_grid[r][c] = value
                        except Exception:
                            pass

            logger.info("DEM cargado en elevation_grid (%dx%d)", self.rows, self.cols)
        except Exception as e:
            logger.error("Error al leer el DEM: %s", e)

    # ------------------------------------------------------------------
    # WindNinja simulation & fallback
    # ------------------------------------------------------------------

    def _locate_output(self, base: str, suffixes: list[str]) -> str | None:
        for suffix in suffixes:
            path = base + suffix
            if os.path.exists(path):
                return path
        return None

    def _read_grid_from_raster(self, path: str, grid: list[list[float]]) -> None:
        try:
            with rasterio.open(path) as dataset:
                band = dataset.read(1)
                nodata = dataset.nodata
                for r in range(self.rows):
                    for c in range(self.cols):
                        lat, lng = self._grid_to_geo(r, c)
                        try:
                            rr, rc = dataset.index(lng, lat)
                            if 0 <= rr < band.shape[0] and 0 <= rc < band.shape[1]:
                                val = float(band[rr, rc])
                                if nodata is None or val != nodata:
                                    grid[r][c] = val
                        except Exception:
                            pass
        except Exception as e:
            logger.warning("Error leyendo raster %s: %s", path, e)

    def _run_windninja(self) -> None:
        self.wind_speed_grid = [
            [self.wind_speed for _ in range(self.cols)] for _ in range(self.rows)
        ]
        self.wind_direction_grid = [
            [self.wind_direction for _ in range(self.cols)] for _ in range(self.rows)
        ]

        if not os.path.exists(DEM_PATH):
            logger.info("DEM no disponible, omitiendo WindNinja")
            return

        try:
            cmd = [
                WINDNINJA_CLI_PATH,
                "--elevation_file", DEM_PATH,
                "--input_speed", str(self.wind_speed),
                "--input_direction", str(self.wind_direction),
                "--output_speed_units", "kph",
                "--mesh_resolution", "100",
                "--vegetation", "grass",
                "--num_threads", "4",
            ]
            logger.info("Ejecutando WindNinja: %s", " ".join(cmd))
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            if result.returncode != 0:
                logger.warning("WindNinja retornó código %d: %s", result.returncode, result.stderr[:300])
                raise RuntimeError("WindNinja falló")

            logger.info("WindNinja completado exitosamente")

            base = os.path.splitext(DEM_PATH)[0]
            vel_path = self._locate_output(base, [
                "_vel.tif", "_speed.tif", "_vel.asc", "_speed.asc",
            ])
            ang_path = self._locate_output(base, [
                "_ang.tif", "_direction.tif", "_ang.asc", "_direction.asc",
            ])

            if vel_path and ang_path:
                self.wind_speed_grid = [
                    [0.0 for _ in range(self.cols)] for _ in range(self.rows)
                ]
                self.wind_direction_grid = [
                    [0.0 for _ in range(self.cols)] for _ in range(self.rows)
                ]
                self._read_grid_from_raster(vel_path, self.wind_speed_grid)
                self._read_grid_from_raster(ang_path, self.wind_direction_grid)
                logger.info("Viento local cargado desde WindNinja")
            else:
                logger.warning("No se encontraron archivos de salida de WindNinja, usando fallback")
                self._apply_wind_fallback()

        except FileNotFoundError:
            logger.warning("WindNinja no está instalado en %s", WINDNINJA_CLI_PATH)
            self._apply_wind_fallback()
        except (subprocess.TimeoutExpired, OSError, Exception) as e:
            logger.warning("WindNinja no disponible (%s), usando fallback matemático", e)
            self._apply_wind_fallback()

    def _apply_wind_fallback(self) -> None:
        for r in range(self.rows):
            for c in range(self.cols):
                elev = self._get_elevation(r, c)
                factor = 1.0 + (elev - 1000.0) / 1000.0
                self.wind_speed_grid[r][c] = max(0.0, self.wind_speed * factor)
                self.wind_direction_grid[r][c] = self.wind_direction

    # ------------------------------------------------------------------
    # Zone bounds & grid
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Satellite greenness (vegetation from Esri World Imagery)
    # ------------------------------------------------------------------

    def _lat_lng_to_tile(self, lat: float, lng: float, zoom: int) -> tuple[int, int]:
        n = 2.0 ** zoom
        x_tile = int((lng + 180.0) / 360.0 * n)
        lat_rad = math.radians(lat)
        y_tile = int(
            (1.0 - math.log(math.tan(lat_rad) + 1.0 / math.cos(lat_rad)) / math.pi)
            / 2.0
            * n
        )
        return x_tile, y_tile

    def _geo_to_tile_pixel(
        self, lat: float, lng: float, zoom: int
    ) -> tuple[int, int, int, int]:
        n = 2.0 ** zoom
        x_pixel = (lng + 180.0) / 360.0 * n * 256.0
        lat_rad = math.radians(lat)
        y_pixel = (
            (1.0 - math.log(math.tan(lat_rad) + 1.0 / math.cos(lat_rad)) / math.pi)
            / 2.0
            * n
            * 256.0
        )
        tx = int(x_pixel) // 256
        ty = int(y_pixel) // 256
        px = int(x_pixel) % 256
        py = int(y_pixel) % 256
        return tx, ty, px, py

    def _pick_zoom_level(self) -> int:
        lat_min, lat_max, lng_min, lng_max = self._zone_bounds
        lng_span = lng_max - lng_min
        if lng_span < 1e-8:
            lng_span = 0.001
        target_px_per_cell = 6.0
        z = math.log2(target_px_per_cell * 360.0 * self.GRID_SIZE / (lng_span * 256.0))
        return max(12, min(18, int(round(z))))

    def _compute_vegetation_greenness(self) -> None:
        self.greenness_grid = [
            [0.0 for _ in range(self.cols)] for _ in range(self.rows)
        ]

        if os.path.exists(GREENNESS_PATH):
            try:
                with open(GREENNESS_PATH, "r") as f:
                    cached = json.load(f)
                if cached.get("bounds") == list(self._zone_bounds):
                    self.greenness_grid = cached["grid"]
                    logger.info(
                        "Greenness cargado desde cache (%d celdas)",
                        len(self.greenness_grid),
                    )
                    return
            except Exception:
                logger.warning("Error leyendo cache de greenness, recalculando...")

        zoom = self._pick_zoom_level()
        needed: dict[tuple[int, int], list[tuple[int, int, int, int]]] = {}

        for r in range(self.rows):
            for c in range(self.cols):
                lat, lng = self._grid_to_geo(r, c)
                tx, ty, px, py = self._geo_to_tile_pixel(lat, lng, zoom)
                key = (tx, ty)
                if key not in needed:
                    needed[key] = []
                needed[key].append((r, c, px, py))

        logger.info(
            "Descargando %d tile(s) satelitales (zoom %d) para greenness...",
            len(needed),
            zoom,
        )

        tiles: dict[tuple[int, int], Image.Image] = {}
        for tx, ty in needed:
            url = (
                f"https://server.arcgisonline.com/ArcGIS/rest/services"
                f"/World_Imagery/MapServer/tile/{zoom}/{ty}/{tx}"
            )
            try:
                resp = requests.get(url, timeout=15)
                if resp.status_code == 200:
                    img = Image.open(io.BytesIO(resp.content)).convert("RGB")
                    tiles[(tx, ty)] = img
            except Exception as e:
                logger.warning("Error descargando tile %d,%d: %s", tx, ty, e)

        if not tiles:
            logger.warning(
                "No se pudieron descargar tiles satelitales, "
                "usando greenness basado en elevación"
            )
            self._greenness_fallback()
            self._cache_greenness()
            return

        for (tx, ty), cells in needed.items():
            img = tiles.get((tx, ty))
            if img is None:
                continue
            for r, c, px, py in cells:
                if 0 <= px < 256 and 0 <= py < 256:
                    R, G, B = img.getpixel((px, py))
                    green = max(0.0, (G - R) / (R + G + B + 1) * 3.0)
                    self.greenness_grid[r][c] = min(1.0, green)

        self._cache_greenness()
        sampled = sum(1 for row in self.greenness_grid for v in row if v > 0)
        logger.info(
            "Greenness calculado desde satélite (%d celdas con vegetación, zoom %d)",
            sampled,
            zoom,
        )

    def _greenness_fallback(self) -> None:
        for r in range(self.rows):
            for c in range(self.cols):
                elev = self._get_elevation(r, c)
                if elev >= 2000:
                    self.greenness_grid[r][c] = 0.15
                elif elev >= 1500:
                    self.greenness_grid[r][c] = 0.30
                elif elev >= 1000:
                    self.greenness_grid[r][c] = 0.50
                elif elev >= 500:
                    self.greenness_grid[r][c] = 0.65
                else:
                    self.greenness_grid[r][c] = 0.80

    def _cache_greenness(self) -> None:
        try:
            os.makedirs(DEM_DIR, exist_ok=True)
            with open(GREENNESS_PATH, "w") as f:
                json.dump(
                    {
                        "bounds": list(self._zone_bounds),
                        "grid": self.greenness_grid,
                    },
                    f,
                )
            logger.info("Greenness cacheado en %s", GREENNESS_PATH)
        except Exception as e:
            logger.warning("Error cacheando greenness: %s", e)

    def _build_grid(self) -> None:
        self.grid = [
            [CellStatus.COMBUSTIBLE for _ in range(self.cols)]
            for _ in range(self.rows)
        ]
        self.burn_remaining = [
            [0 for _ in range(self.cols)] for _ in range(self.rows)
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

    # ------------------------------------------------------------------
    # INP physical model helpers
    # ------------------------------------------------------------------

    def _get_elevation(self, r: int, c: int) -> float:
        if self.elevation_grid is not None and self.elevation_grid:
            try:
                return self.elevation_grid[r][c]
            except IndexError:
                pass
        return DEFAULT_ELEVATION

    def _get_slope_factor(self, r1: int, c1: int, r2: int, c2: int) -> float:
        e1 = self._get_elevation(r1, c1)
        e2 = self._get_elevation(r2, c2)
        elev_diff = e2 - e1
        lat_min, lat_max, lng_min, lng_max = self._zone_bounds
        lat_per_cell = (lat_max - lat_min) / self.rows
        lng_per_cell = (lng_max - lng_min) / self.cols
        dr = (r2 - r1) * lat_per_cell
        dc = (c2 - c1) * lng_per_cell
        distance = math.hypot(dr, dc)
        if distance < 1e-10:
            return 1.0
        slope_ratio = elev_diff / distance
        P = 1.0 + slope_ratio * 3.0
        return max(0.3, min(P, 3.0))

    def _get_vegetation_params(self, r: int, c: int) -> tuple[float, float, float]:
        g = 0.5
        if self.greenness_grid is not None and r < len(self.greenness_grid):
            try:
                g = self.greenness_grid[r][c]
            except IndexError:
                pass
        if g > 0.60:
            return (1.2, 0.3, 0.05)
        elif g > 0.40:
            return (1.1, 0.5, 0.15)
        elif g > 0.25:
            return (1.0, 0.7, 0.30)
        elif g > 0.10:
            return (0.8, 0.9, 0.50)
        else:
            return (0.5, 1.1, 0.80)

    def _spread_probability_inp(
        self,
        r: int,
        c: int,
        nr: int,
        nc: int,
        neighbor_angle: float,
    ) -> float:
        local_speed = (
            self.wind_speed_grid[nr][nc]
            if self.wind_speed_grid is not None
            else self.wind_speed
        )
        local_dir = (
            self.wind_direction_grid[nr][nc]
            if self.wind_direction_grid is not None
            else self.wind_direction
        )
        K, C, h_extra = self._get_vegetation_params(nr, nc)
        P = self._get_slope_factor(r, c, nr, nc)
        H = max(0.01, self._humidity * 0.35 + h_extra)

        wind_toward = (360 - local_dir) % 360
        angle_diff = (neighbor_angle - wind_toward + 360) % 360
        if angle_diff > 180:
            angle_diff = 360 - angle_diff
        alignment = math.cos(math.radians(angle_diff))

        # Background spread (radiation + flame contact) — omnidirectional
        if alignment > 0:
            background = 0.12
        else:
            background = 0.04 + 0.04 * (1.0 + alignment)

        # Wind-driven component (INP formula)
        if alignment > 0 and local_speed > 0:
            V_eff = local_speed * alignment
            INP = (K * C * P * (V_eff ** 2)) / H
            wind_prob = INP / (INP + 1.0)
        else:
            # Pequeña probabilidad contra el viento (pavesas, remolinos)
            wind_prob = 0.0
            if alignment < 0 and local_speed > 0:
                V_up = local_speed * (-alignment)
                INP_up = 0.03 * (K * C * P * (V_up ** 2)) / H
                wind_prob = min(0.25, INP_up / (INP_up + 1.0))

        prob = min(background + wind_prob, 0.95)
        g = 0.5
        if self.greenness_grid is not None:
            try:
                g = self.greenness_grid[nr][nc]
            except IndexError:
                pass
        prob *= max(0.05, g)
        return prob

    # ------------------------------------------------------------------
    # Control
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Simulation step & propagation (INP-based)
    # ------------------------------------------------------------------

    def _step(self) -> list[dict]:
        new_grid = [row[:] for row in self.grid]
        new_burn = (
            [row[:] for row in self.burn_remaining]
            if self.burn_remaining is not None
            else None
        )
        updates: list[dict] = []

        for r in range(self.rows):
            for c in range(self.cols):
                if self.grid[r][c] == CellStatus.FUEGO:
                    if random.random() < 0.55:
                        self._spread_to_neighbors(r, c, new_grid, new_burn, updates)

                    g = 0.5
                    if self.greenness_grid is not None:
                        try:
                            g = self.greenness_grid[r][c]
                        except IndexError:
                            pass
                    if g < 0.10:
                        if random.random() < 0.95:
                            new_grid[r][c] = CellStatus.QUEMADO
                            updates.append({"row": r, "col": c, "status": CellStatus.QUEMADO})
                            if new_burn is not None:
                                new_burn[r][c] = 0
                            continue
                    elif g < 0.25:
                        if random.random() < 0.70:
                            new_grid[r][c] = CellStatus.QUEMADO
                            updates.append({"row": r, "col": c, "status": CellStatus.QUEMADO})
                            if new_burn is not None:
                                new_burn[r][c] = 0
                            continue

                    if new_burn is not None:
                        new_burn[r][c] -= 1
                        if new_burn[r][c] <= 0:
                            new_grid[r][c] = CellStatus.QUEMADO
                            updates.append({"row": r, "col": c, "status": CellStatus.QUEMADO})
                    else:
                        new_grid[r][c] = CellStatus.QUEMADO
                        updates.append({"row": r, "col": c, "status": CellStatus.QUEMADO})

        self.grid = new_grid
        if new_burn is not None:
            self.burn_remaining = new_burn
        return updates

    def _spread_to_neighbors(
        self,
        r: int,
        c: int,
        new_grid: list[list[CellStatus]],
        new_burn: list[list[int]] | None,
        updates: list[dict],
    ) -> None:
        base_dirs = [
            (-1, -1, 225),
            (-1, 0, 180),
            (-1, 1, 135),
            (0, -1, 270),
            (0, 1, 90),
            (1, -1, 315),
            (1, 0, 0),
            (1, 1, 45),
        ]

        random.shuffle(base_dirs)
        check_count = random.randint(4, 6)

        for dr, dc, angle in base_dirs[:check_count]:
            nr, nc = r + dr, c + dc
            if not (0 <= nr < self.rows and 0 <= nc < self.cols):
                continue
            if new_grid[nr][nc] != CellStatus.COMBUSTIBLE:
                continue

            prob = self._spread_probability_inp(r, c, nr, nc, angle)
            noise = random.uniform(0.75, 1.25)
            prob = min(prob * noise, 0.95)
            if prob > 0 and random.random() < prob:
                new_grid[nr][nc] = CellStatus.FUEGO
                if new_burn is not None:
                    g = 0.5
                    if self.greenness_grid is not None:
                        try:
                            g = self.greenness_grid[nr][nc]
                        except IndexError:
                            pass
                    if g < 0.10:
                        max_burn = 1
                    elif g < 0.25:
                        max_burn = 2
                    else:
                        max_burn = 3
                    new_burn[nr][nc] = random.randint(1, max_burn)
                updates.append({"row": nr, "col": nc, "status": CellStatus.FUEGO})

        # Extra tirada para atrás (15% de la probabilidad INP)
        for dr, dc, angle in base_dirs:
            nr, nc = r + dr, c + dc
            if not (0 <= nr < self.rows and 0 <= nc < self.cols):
                continue
            if new_grid[nr][nc] != CellStatus.COMBUSTIBLE:
                continue

            prob = self._spread_probability_inp(r, c, nr, nc, angle)
            prob *= 0.10

            if random.random() < prob:
                new_grid[nr][nc] = CellStatus.FUEGO
                if new_burn is not None:
                    g = 0.5
                    if self.greenness_grid is not None:
                        try:
                            g = self.greenness_grid[nr][nc]
                        except IndexError:
                            pass
                    if g < 0.10:
                        max_burn = 1
                    elif g < 0.25:
                        max_burn = 2
                    else:
                        max_burn = 3
                    new_burn[nr][nc] = random.randint(1, max_burn)
                updates.append({"row": nr, "col": nc, "status": CellStatus.FUEGO})

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _has_burning(self) -> bool:
        return any(
            cell == CellStatus.FUEGO for row in self.grid for cell in row
        )

    @property
    def active(self) -> bool:
        return self.running and not self.paused
