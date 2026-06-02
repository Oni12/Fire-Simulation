import { Component, input, output } from '@angular/core';
import { WindData } from '../../../../shared/interfaces';

@Component({
  selector: 'app-control-panel',
  imports: [],
  templateUrl: './control-panel.html',
  styleUrl: './control-panel.css',
})
export class ControlPanel {
  windData = input<WindData | null>(null);
  canStart = input(false);
  isRunning = input(false);
  isPaused = input(false);

  startSimulation = output<void>();
  pauseSimulation = output<void>();
  stopSimulation = output<void>();
}
