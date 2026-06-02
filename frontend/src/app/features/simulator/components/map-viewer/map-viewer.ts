import { Component, input, output, effect, inject, NgZone } from '@angular/core';
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

  private ngZone = inject(NgZone);

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

    L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', {
      maxZoom: 19,
      attribution: 'Esri, Maxar, Earthstar Geographics',
    }).addTo(this.map);

    this.map.on('click', (e: L.LeafletMouseEvent) => {
      this.ngZone.run(() => {
        this.placeIgnitionMarker(e.latlng);
        this.ignitionPointChange.emit([e.latlng.lat, e.latlng.lng]);
      });
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

  private readonly gridSize = 40;

  private drawGrid(updates: CellUpdate[]): void {
    if (this.gridLayer) {
      this.gridLayer.clearLayers();
    } else {
      this.gridLayer = L.layerGroup().addTo(this.map!);
    }

    if (!updates.length) return;

    const bounds = this.polygon?.getBounds();
    if (!bounds) return;

    const maxLat = bounds.getNorth();
    const minLat = bounds.getSouth();
    const minLng = bounds.getWest();
    const maxLng = bounds.getEast();

    const latSpan = maxLat - minLat;
    const lngSpan = maxLng - minLng;
    const latStep = latSpan / this.gridSize;
    const lngStep = lngSpan / this.gridSize;

    for (const update of updates) {
      const cellMaxLat = maxLat - update.row * latStep;
      const cellMinLat = maxLat - (update.row + 1) * latStep;
      const cellMinLng = minLng + update.col * lngStep;
      const cellMaxLng = minLng + (update.col + 1) * lngStep;

      const color =
        update.status === 'fuego'
          ? '#e63946'
          : update.status === 'quemado'
          ? '#4a4a4a'
          : 'transparent';

      if (color !== 'transparent') {
        L.rectangle(
          [
            [cellMaxLat, cellMinLng],
            [cellMinLat, cellMaxLng],
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
