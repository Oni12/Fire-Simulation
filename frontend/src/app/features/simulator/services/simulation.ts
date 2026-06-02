import { Injectable } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { webSocket, WebSocketSubject } from 'rxjs/webSocket';
import { Observable, map } from 'rxjs';
import { InitialData, SimulationConfig, CellUpdate } from '../../../shared/interfaces';

const API_BASE = 'http://localhost:8000';
const WS_URL = 'ws://localhost:8000/api/simulation/ws';

@Injectable({
  providedIn: 'root',
})
export class SimulationService {
  private wsSubject: WebSocketSubject<CellUpdate[] | { action: string; config?: SimulationConfig }>;

  constructor(private http: HttpClient) {
    this.wsSubject = webSocket<CellUpdate[] | { action: string; config?: SimulationConfig }>(WS_URL);
  }

  getInitialData(): Observable<InitialData> {
    return this.http.get<InitialData>(`${API_BASE}/api/simulation/initial`);
  }

  get cellUpdates(): Observable<CellUpdate[]> {
    return this.wsSubject.pipe(
      map((msg) => (Array.isArray(msg) ? msg : []))
    ) as Observable<CellUpdate[]>;
  }

  startSimulation(config: SimulationConfig): void {
    this.wsSubject.next({ action: 'start', config });
  }

  pauseSimulation(): void {
    this.wsSubject.next({ action: 'pause' });
  }

  stopSimulation(): void {
    this.wsSubject.next({ action: 'stop' });
  }

  disconnect(): void {
    this.wsSubject.complete();
  }
}
