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
  zonePolygons = input<[number, number][][]>([]);
  cellUpdates = input<CellUpdate[]>([]);
  ignitionPointChange = output<[number, number]>();

  private map?: L.Map;
  private polygons: L.Polygon[] = [];
  private marker?: L.Marker;
  private gridLayer?: L.LayerGroup;
  private ignitionDivIcon = L.divIcon({
    html: '🔥',
    className: 'fire-marker',
    iconSize: [24, 24],
    iconAnchor: [12, 24],
  });

  private ngZone = inject(NgZone);
  private burnedCells = new Set<string>();

  constructor() {
    effect(() => {
      const polygons = this.zonePolygons();
      if (polygons.length && this.map) {
        this.drawZones(polygons);
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
    const polygons = this.zonePolygons();
    if (polygons.length) {
      this.drawZones(polygons);
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

  private drawZones(polygons: [number, number][][]): void {
    this.polygons.forEach((p) => p.remove());
    this.polygons = [];

    const allBounds = L.latLngBounds([]);
    for (const coords of polygons) {
      const polygon = L.polygon(coords, {
        color: '#e63946',
        fillColor: '#e63946',
        fillOpacity: 0.15,
        weight: 2,
      }).addTo(this.map!);
      this.polygons.push(polygon);
      allBounds.extend(polygon.getBounds());
    }
    this.map!.fitBounds(allBounds.pad(0.1));
  }

  private placeIgnitionMarker(latlng: L.LatLng): void {
    if (this.marker) {
      this.marker.remove();
    }
    this.marker = L.marker(latlng, {
      icon: this.ignitionDivIcon,
    }).addTo(this.map!);
  }

  private readonly gridSize = 100;

  private drawGrid(updates: CellUpdate[]): void {
    if (!this.gridLayer) {
      this.gridLayer = L.layerGroup().addTo(this.map!);
    }

    if (!this.polygons.length) return;
    const bounds = this.polygons[0].getBounds();
    for (let i = 1; i < this.polygons.length; i++) {
      bounds.extend(this.polygons[i].getBounds());
    }

    const maxLat = bounds.getNorth();
    const minLat = bounds.getSouth();
    const minLng = bounds.getWest();
    const maxLng = bounds.getEast();

    const latSpan = maxLat - minLat;
    const lngSpan = maxLng - minLng;
    const latStep = latSpan / this.gridSize;
    const lngStep = lngSpan / this.gridSize;

    for (const update of updates) {
      if (update.status === 'quemado') {
        this.burnedCells.add(`${update.row},${update.col}`);
      }
    }

    this.gridLayer.clearLayers();

    for (const key of this.burnedCells) {
      const [r, c] = key.split(',').map(Number);
      const cellMaxLat = maxLat - r * latStep;
      const cellMinLat = maxLat - (r + 1) * latStep;
      const cellMinLng = minLng + c * lngStep;
      const cellMaxLng = minLng + (c + 1) * lngStep;
      L.rectangle(
        [[cellMaxLat, cellMinLng], [cellMinLat, cellMaxLng]],
        { color: '#555555', fillColor: '#555555', fillOpacity: 0.45, weight: 0 }
      ).addTo(this.gridLayer);
    }

    for (const update of updates) {
      if (update.status !== 'fuego') continue;
      const cellMaxLat = maxLat - update.row * latStep;
      const cellMinLat = maxLat - (update.row + 1) * latStep;
      const cellMinLng = minLng + update.col * lngStep;
      const cellMaxLng = minLng + (update.col + 1) * lngStep;
      L.rectangle(
        [[cellMaxLat, cellMinLng], [cellMinLat, cellMaxLng]],
        { color: '#ef4444', fillColor: '#ef4444', fillOpacity: 0.7, weight: 0 }
      ).addTo(this.gridLayer);
    }
  }

}
