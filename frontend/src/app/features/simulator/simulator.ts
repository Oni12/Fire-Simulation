import { Component, OnInit, OnDestroy, signal, computed } from '@angular/core';
import { SimulationService } from './services/simulation';
import { ControlPanel } from './components/control-panel/control-panel';
import { MapViewer } from './components/map-viewer/map-viewer';
import { CellUpdate } from '../../shared/interfaces';
import { Subscription } from 'rxjs';

@Component({
  selector: 'app-simulator',
  standalone: true,
  imports: [ControlPanel, MapViewer],
  templateUrl: './simulator.html',
  styleUrls: ['./simulator.css'],
})
export class SimulatorComponent implements OnInit, OnDestroy {
  zonePolygons = signal<[number, number][][]>([]);
  cellUpdates = signal<CellUpdate[]>([]);
  ignitionPoint = signal<[number, number] | null>(null);
  windSpeed = signal(15.5);
  windDirection = signal(180);
  isRunning = signal(false);
  isPaused = signal(false);
  loading = signal(true);

  canStart = computed(() => this.ignitionPoint() !== null && !this.isRunning());

  private subs: Subscription[] = [];

  constructor(private simulation: SimulationService) {}

  ngOnInit(): void {
    this.subs.push(
      this.simulation.getInitialData().subscribe({
        next: (data) => {
          this.zonePolygons.set(data.zonePolygons);
          this.windSpeed.set(data.wind.speed);
          this.windDirection.set(data.wind.direction);
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

  onStart(): void {
    const point = this.ignitionPoint();
    if (!point) return;
    this.simulation.startSimulation({
      windSpeed: this.windSpeed(),
      windDirection: this.windDirection(),
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
