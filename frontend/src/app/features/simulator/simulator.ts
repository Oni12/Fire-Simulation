import { Component, OnInit, OnDestroy, signal } from '@angular/core';
import { SimulationService } from './services/simulation';
import { ControlPanel } from './components/control-panel/control-panel';
import { MapViewer } from './components/map-viewer/map-viewer';
import { WindData, CellUpdate } from '../../shared/interfaces';
import { Subscription } from 'rxjs';

@Component({
  selector: 'app-simulator',
  standalone: true,
  imports: [ControlPanel, MapViewer],
  templateUrl: './simulator.html',
  styleUrls: ['./simulator.css'],
})
export class SimulatorComponent implements OnInit, OnDestroy {
  windData = signal<WindData | null>(null);
  zoneCoordinates = signal<[number, number][]>([]);
  cellUpdates = signal<CellUpdate[]>([]);
  ignitionPoint = signal<[number, number] | null>(null);
  isRunning = signal(false);
  isPaused = signal(false);
  loading = signal(true);

  private subs: Subscription[] = [];

  constructor(private simulation: SimulationService) {}

  ngOnInit(): void {
    this.subs.push(
      this.simulation.getInitialData().subscribe({
        next: (data) => {
          this.windData.set(data.wind);
          this.zoneCoordinates.set(data.zoneCoordinates);
          this.loading.set(false);
        },
        error: () => this.loading.set(false),
      })
    );

    this.subs.push(
      this.simulation.cellUpdates.subscribe((updates) => {
        this.cellUpdates.set(updates);
      })
    );
  }

  ngOnDestroy(): void {
    this.subs.forEach((s) => s.unsubscribe());
    this.simulation.disconnect();
  }

  onIgnitionPointChange(point: [number, number]): void {
    this.ignitionPoint.set(point);
  }

  get canStart(): boolean {
    return this.ignitionPoint() !== null && !this.isRunning();
  }

  onStart(): void {
    const wind = this.windData();
    const point = this.ignitionPoint();
    if (!wind || !point) return;
    this.simulation.startSimulation({
      windSpeed: wind.speed,
      windDirection: wind.direction,
      ignitionPoint: point,
    });
    this.isRunning.set(true);
    this.isPaused.set(false);
  }

  onPause(): void {
    this.simulation.pauseSimulation();
    this.isPaused.set(true);
  }

  onStop(): void {
    this.simulation.stopSimulation();
    this.isRunning.set(false);
    this.isPaused.set(false);
  }
}
