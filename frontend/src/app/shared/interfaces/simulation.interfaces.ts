export interface WindData {
  speed: number;
  direction: number;
}

export interface SimulationConfig {
  windSpeed: number;
  windDirection: number;
  ignitionPoint: [number, number];
}

export interface CellUpdate {
  row: number;
  col: number;
  status: 'combustible' | 'fuego' | 'quemado';
}

export interface InitialData {
  zonePolygons: [number, number][][];
  wind: WindData;
}
