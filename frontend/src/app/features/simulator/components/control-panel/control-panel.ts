import { Component, input, output } from '@angular/core';

@Component({
  selector: 'app-control-panel',
  imports: [],
  templateUrl: './control-panel.html',
  styleUrl: './control-panel.css',
})
export class ControlPanel {
  windSpeed = input(0);
  windDirection = input(0);
  canStart = input(false);
  isRunning = input(false);
  isPaused = input(false);

  windSpeedChange = output<number>();
  windDirectionChange = output<number>();
  startSimulation = output<void>();
  pauseSimulation = output<void>();
  stopSimulation = output<void>();

  onSpeedInput(event: Event) {
    const val = parseFloat((event.target as HTMLInputElement).value);
    if (!isNaN(val)) this.windSpeedChange.emit(val);
  }

  onDirectionInput(event: Event) {
    const val = parseFloat((event.target as HTMLInputElement).value);
    if (!isNaN(val)) this.windDirectionChange.emit(val);
  }
}
