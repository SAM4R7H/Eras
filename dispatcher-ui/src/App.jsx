import { useEffect, useState } from 'react';
import { MapContainer, TileLayer, Marker, Popup, Polyline } from 'react-leaflet';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';

// FIX: Leaflet's default icon breaks with bundlers — set paths explicitly
delete L.Icon.Default.prototype._getIconUrl;
L.Icon.Default.mergeOptions({
  iconRetinaUrl: 'https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon-2x.png',
  iconUrl:       'https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon.png',
  shadowUrl:     'https://unpkg.com/leaflet@1.9.4/dist/images/marker-shadow.png',
});

const stationIcon = new L.Icon({
  iconUrl: 'https://raw.githubusercontent.com/pointhi/leaflet-color-markers/master/img/marker-icon-red.png',
  shadowUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/0.7.7/images/marker-shadow.png',
  iconSize: [25, 41], iconAnchor: [12, 41], popupAnchor: [1, -34],
});

const incidentIcon = new L.Icon({
  iconUrl: 'https://raw.githubusercontent.com/pointhi/leaflet-color-markers/master/img/marker-icon-orange.png',
  shadowUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/0.7.7/images/marker-shadow.png',
  iconSize: [25, 41], iconAnchor: [12, 41], popupAnchor: [1, -34],
});

// Safe polyline renderer — validates coords before rendering
const SafePolyline = ({ detail, incId }) => {
  if (!detail || !Array.isArray(detail.route_shape)) return null;
  const validCoords = detail.route_shape.filter(coord =>
    Array.isArray(coord) && coord.length === 2 &&
    typeof coord[0] === 'number' && typeof coord[1] === 'number'
  );
  if (validCoords.length < 2) return null;
  return (
    <Polyline
      key={`${incId}-${detail.unit_id}`}
      positions={validCoords}
      color="#2196F3"
      weight={4}
      opacity={0.8}
    />
  );
};

export default function App() {
  const [incidents, setIncidents] = useState([]);
  const [stations, setStations] = useState([]);

  const fetchData = () => {
    fetch('http://127.0.0.1:8000/system-status')
      .then(res => res.json())
      .then(data => {
        setIncidents(data.active_incidents || []);
        setStations(data.stations || []);
      })
      .catch(() => console.log('Backend offline'));
  };

  useEffect(() => {
    fetchData();
    let ws;
    try {
      ws = new WebSocket('ws://127.0.0.1:8000/ws');
      ws.onmessage = (event) => {
        const data = JSON.parse(event.data);
        // FIX: also refetch on status updates so map stays in sync
        if (['NEW_INCIDENT', 'INCIDENT_RESOLVED', 'STATUS_UPDATE'].includes(data.type)) {
          fetchData();
        }
      };
      ws.onerror = () => console.log('WS error — polling fallback');
    } catch (e) {}

    // FIX: poll every 5s as fallback in case WS drops
    const poll = setInterval(fetchData, 5000);
    return () => {
      if (ws) ws.close();
      clearInterval(poll);
    };
  }, []);

  const handleResolve = (incident_id) => {
    fetch(`http://127.0.0.1:8000/incidents/${incident_id}/resolve`, { method: 'POST' })
      .then(() => fetchData());
  };

  const activeIncidents = incidents.filter(inc => inc.status !== 'Resolved');

  return (
    <div style={{ display: 'flex', height: '100vh', width: '100vw', fontFamily: 'sans-serif' }}>

      {/* SIDEBAR */}
      <div style={{ width: '350px', backgroundColor: '#1e1e1e', color: '#fff', overflowY: 'auto', padding: '20px' }}>
        <h2 style={{ marginTop: 0, borderBottom: '2px solid #333', paddingBottom: '10px' }}>
          🚨 Live CAD Feed
        </h2>

        {activeIncidents.length === 0 && (
          <p style={{ color: '#888' }}>No active incidents.</p>
        )}

        {activeIncidents.map((inc) => (
          <div
            key={inc.incident_id}
            style={{
              backgroundColor: '#2a2a2a',
              padding: '15px',
              borderRadius: '8px',
              marginBottom: '15px',
              borderLeft: inc.status === 'Dispatched' ? '5px solid #4CAF50' : '5px solid #FF9800',
            }}
          >
            <h3 style={{ margin: '0 0 10px 0' }}>{inc.incident_id} — {inc.type}</h3>
            <p style={{ margin: '5px 0', fontSize: '14px' }}><strong>Priority:</strong> {inc.priority}</p>
            <p style={{ margin: '5px 0', fontSize: '14px' }}><strong>Status:</strong> {inc.status}</p>

            <div style={{
              margin: '10px 0', fontSize: '14px', color: '#aaa',
              backgroundColor: '#111', padding: '8px', borderRadius: '4px'
            }}>
              <strong>AI Routing Output:</strong>
              {inc.dispatch_details && inc.dispatch_details.length > 0
                ? inc.dispatch_details.map(d => (
                  <div key={d.unit_id} style={{ marginTop: '4px' }}>
                    ↳ <strong>{d.unit_id}</strong> ({d.distance} mi)
                  </div>
                ))
                : <div style={{ marginTop: '4px', color: '#666' }}>No units available</div>
              }
            </div>

            <button
              onClick={() => handleResolve(inc.incident_id)}
              style={{
                marginTop: '10px', padding: '8px', backgroundColor: '#4CAF50',
                color: 'white', border: 'none', borderRadius: '4px',
                cursor: 'pointer', width: '100%',
              }}
            >
              ✅ Resolve
            </button>
          </div>
        ))}
      </div>

      {/* MAP */}
      <div style={{ flex: 1 }}>
        <MapContainer center={[40.7200, -73.9900]} zoom={13} style={{ height: '100%', width: '100%' }}>
          <TileLayer url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png" />

          {/* Station markers */}
          {stations.map((station) => (
            <Marker
              key={station.station_id}
              position={[station.location.lat, station.location.lng]}
              icon={stationIcon}
            >
              <Popup><strong>🚒 {station.name}</strong></Popup>
            </Marker>
          ))}

          {/* Incident markers + route lines
              FIX: was <div> wrapper — React elements inside MapContainer must NOT
              be wrapped in HTML elements. Use React Fragment <> instead. */}
          {activeIncidents.map((inc) => (
            <>
              <Marker
                key={`marker-${inc.incident_id}`}
                position={[inc.location.lat, inc.location.lng]}
                icon={incidentIcon}
              >
                <Popup>
                  <strong>🚨 {inc.incident_id}</strong><br />
                  Type: {inc.type}<br />
                  Priority: {inc.priority}<br />
                  Status: {inc.status}
                </Popup>
              </Marker>

              {inc.dispatch_details?.map((detail) => (
                <SafePolyline
                  key={`line-${inc.incident_id}-${detail.unit_id}`}
                  detail={detail}
                  incId={inc.incident_id}
                />
              ))}
            </>
          ))}

        </MapContainer>
      </div>
    </div>
  );
}
