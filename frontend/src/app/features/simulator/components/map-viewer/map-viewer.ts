import { Component, input, output, effect, inject, DestroyRef, signal } from '@angular/core';
import { CellUpdate } from '../../../../shared/interfaces';
import * as L from 'leaflet';

@Component({
  selector: 'app-map-viewer',
  imports: [],
  templateUrl: './map-viewer.html',
  styleUrl: './map-viewer.css',
})
export class MapViewer {
  zoneCoordinates = input<[number, number][]>([]);
  cellUpdates = input<CellUpdate[]>([]);
  ignitionPointChange = output<[number, number]>();

  private map?: L.Map;
  private polygon?: L.Polygon;
  private marker?: L.Marker;
  private gridLayer?: L.LayerGroup;
  private ignitionDivIcon = L.divIcon({
    html: '🔥',
    className: 'fire-marker',
    iconSize: [24, 24],
    iconAnchor: [12, 24],
  });

  private destroyRef = inject(DestroyRef);

  constructor() {
    effect(() => {
      const coords = this.zoneCoordinates();
      if (coords.length && this.map) {
        this.drawZone(coords);
      }
    });
    effect(() => {
      const updates = this.cellUpdates();
      if (this.map) {
        this.drawGrid(updates);
      }
    });
  }

  ngAfterViewInit(): void {
    this.initMap();
    const coords = this.zoneCoordinates();
    if (coords.length) {
      this.drawZone(coords);
    }
  }

  private initMap(): void {
    this.map = L.map('map', {
      center: [40.4168, -3.7038],
      zoom: 13,
    });

    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 19,
      attribution: '© OpenStreetMap contributors',
    }).addTo(this.map);

    this.map.on('click', (e: L.LeafletMouseEvent) => {
      this.placeIgnitionMarker(e.latlng);
      this.ignitionPointChange.emit([e.latlng.lat, e.latlng.lng]);
    });
  }

  private drawZone(coords: [number, number][]): void {
    if (this.polygon) {
      this.polygon.remove();
    }
    this.polygon = L.polygon(coords, {
      color: '#e63946',
      fillColor: '#e63946',
      fillOpacity: 0.15,
      weight: 2,
    }).addTo(this.map!);
    this.map!.fitBounds(this.polygon.getBounds().pad(0.1));
  }

  private placeIgnitionMarker(latlng: L.LatLng): void {
    if (this.marker) {
      this.marker.remove();
    }
    this.marker = L.marker(latlng, {
      icon: this.ignitionDivIcon,
    }).addTo(this.map!);
  }

  private drawGrid(updates: CellUpdate[]): void {
    if (this.gridLayer) {
      this.gridLayer.clearLayers();
    } else {
      this.gridLayer = L.layerGroup().addTo(this.map!);
    }

    const polyBounds = this.polygon?.getBounds();
    if (!polyBounds) return;

    if (!updates.length) return;

    const bounds = this.polygon!.getBounds();
    const latSpan = bounds.getNorth() - bounds.getSouth();
    const lngSpan = bounds.getEast() - bounds.getWest();

    const maxRow = Math.max(...updates.map((u) => u.row), 0) + 1;
    const maxCol = Math.max(...updates.map((u) => u.col), 0) + 1;

    const cellLat = latSpan / Math.max(maxRow, 1);
    const cellLng = lngSpan / Math.max(maxCol, 1);

    for (const update of updates) {
      const north = bounds.getNorth() - update.row * cellLat;
      const south = north - cellLat;
      const west = bounds.getWest() + update.col * cellLng;
      const east = west + cellLng;

      const color =
        update.status === 'fuego'
          ? '#e63946'
          : update.status === 'quemado'
          ? '#4a4a4a'
          : 'transparent';

      if (color !== 'transparent') {
        L.rectangle(
          [
            [north, west],
            [south, east],
          ],
          {
            color,
            fillColor: color,
            fillOpacity: 0.7,
            weight: 0,
          }
        ).addTo(this.gridLayer!);
      }
    }
  }
}
