import { useEffect, useState, useRef, useCallback } from 'react';
import { MapContainer, TileLayer, Marker, Popup, Polyline, useMap } from 'react-leaflet';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import './index.css';
import { AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer } from 'recharts';

const API = 'http://127.0.0.1:8000';

// Fix Leaflet icon paths broken by Vite bundler
delete L.Icon.Default.prototype._getIconUrl;
L.Icon.Default.mergeOptions({
  iconRetinaUrl: 'https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon-2x.png',
  iconUrl:       'https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon.png',
  shadowUrl:     'https://unpkg.com/leaflet@1.9.4/dist/images/marker-shadow.png',
});

// ── Icon factories ──────────────────────────────────────────────
function makeStationIcon() {
  return L.divIcon({
    html: `<div style="width:28px;height:28px;border-radius:50%;background:#0d1421;border:2px solid #00d4ff;display:flex;align-items:center;justify-content:center;font-size:14px;box-shadow:0 0 8px rgba(0,212,255,.5)">🏢</div>`,
    className: '', iconSize: [28, 28], iconAnchor: [14, 14],
  });
}

function makeIncidentIcon(priority, isSelected) {
  const colors = { P1: '#ff3a5c', P2: '#ffaa00', P3: '#00d4ff', P4: '#5a7a90' };
  const col  = colors[priority] || '#ff3a5c';
  const ring = isSelected ? `outline:3px solid ${col};outline-offset:4px;` : '';
  return L.divIcon({
    html: `<div style="padding:4px 8px;background:#070b14;border:1.5px solid ${col};border-radius:4px;font-size:10px;font-weight:700;color:${col};font-family:monospace;letter-spacing:.5px;box-shadow:0 0 ${isSelected ? '20px' : '10px'} ${col}${isSelected ? 'cc' : '66'};white-space:nowrap;animation:fadeIn .3s ease;${ring}">${priority} ⚠${isSelected ? ' ◉' : ''}</div>`,
    className: '', iconSize: [null, null], iconAnchor: [0, 0],
  });
}

const VEHICLE_ICONS = {
  Engine:    { emoji: '🚒', color: '#ff8c42' },
  Ambulance: { emoji: '🚑', color: '#00d4ff' },
  Ladder:    { emoji: '🚒', color: '#7b2fff' },
};

function makeVehicleIcon(unitType, progress) {
  const info = VEHICLE_ICONS[unitType] || { emoji: '🚐', color: '#00d4ff' };
  const pct  = Math.round(progress * 100);
  return L.divIcon({
    html: `<div style="position:relative;display:flex;flex-direction:column;align-items:center">
      <div style="width:32px;height:32px;border-radius:50%;background:#0d1421;border:2px solid ${info.color};display:flex;align-items:center;justify-content:center;font-size:15px;box-shadow:0 0 12px ${info.color}66">${info.emoji}</div>
      <div style="width:32px;height:3px;background:#0d1421;border-radius:2px;margin-top:2px;overflow:hidden;border:1px solid ${info.color}44">
        <div style="width:${pct}%;height:100%;background:${info.color};border-radius:2px;transition:width .5s"></div>
      </div>
    </div>`,
    className: '', iconSize: [32, 40], iconAnchor: [16, 20],
  });
}

// ── Moving vehicle component ─────────────────────────────────────
function MovingVehicle({ detail, incidentId, incidentStatus }) {
  const map         = useMap();
  const markerRef   = useRef(null);
  const rafRef      = useRef(null);
  const progressRef = useRef(0);
  const startRef    = useRef(null);
  const arrivedRef  = useRef(false);

  useEffect(() => {
    const route = detail?.route_shape;
    if (!route || route.length < 2) return;

    // Precompute cumulative distances
    const segDists = [];
    let totalDist  = 0;
    for (let i = 0; i < route.length - 1; i++) {
      const d = Math.hypot(
        route[i+1][0] - route[i][0],
        route[i+1][1] - route[i][1]
      );
      segDists.push(d);
      totalDist += d;
    }

    const durationMs = Math.min(
      (detail.duration_s || 120) * 1000 * 0.12,
      40000
    );

    // If already completed, place at destination and stop
    if (progressRef.current >= 1.0) {
      if (!markerRef.current) {
        markerRef.current = L.marker(route[route.length - 1], {
          icon: makeVehicleIcon(detail.unit_type, 1.0),
          zIndexOffset: 1000,
        }).addTo(map);
      }
      return;
    }

    // Create marker at current progress position if not yet created
    if (!markerRef.current) {
      markerRef.current = L.marker(route[0], {
        icon: makeVehicleIcon(detail.unit_type, progressRef.current),
        zIndexOffset: 1000,
      }).addTo(map);
    }

    startRef.current = performance.now() - (progressRef.current * durationMs);

    const animate = (now) => {
      const elapsed  = now - startRef.current;
      const progress = Math.min(elapsed / durationMs, 1.0);
      progressRef.current = progress;

      // --- FIXED LOGIC: Explicitly check for 100% arrival ---
      if (progress >= 1.0) {
        if (markerRef.current) {
          markerRef.current.setLatLng(route[route.length - 1]);
          markerRef.current.setIcon(makeVehicleIcon(detail.unit_type, 1.0));
        }
        
        if (!arrivedRef.current) {
          arrivedRef.current = true;
          // NEW: Smart routing ping
          if (incidentStatus === 'Dispatched') {
            fetch(`${API}/incidents/${incidentId}/on-scene`, { method: 'POST' }).catch(() => {});
          } else if (incidentStatus === 'Returning') {
            fetch(`${API}/incidents/${incidentId}/resolve`, { method: 'POST' }).catch(() => {});
          }
        }
        return; 
      }

      // Still moving...
      const target = progress * totalDist;
      let cum      = 0;

      for (let i = 0; i < segDists.length; i++) {
        if (cum + segDists[i] >= target) {
          const t   = segDists[i] > 0 ? (target - cum) / segDists[i] : 0;
          const lat = route[i][0] + (route[i+1][0] - route[i][0]) * t;
          const lng = route[i][1] + (route[i+1][1] - route[i][1]) * t;
          if (markerRef.current) {
            markerRef.current.setLatLng([lat, lng]);
            markerRef.current.setIcon(makeVehicleIcon(detail.unit_type, progress));
          }
          break;
        }
        cum += segDists[i];
      }

      rafRef.current = requestAnimationFrame(animate);
    };

    rafRef.current = requestAnimationFrame(animate);

    return () => {
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
    };
  }, [detail, map, incidentId]);

  // On unmount (incident resolved): remove marker and reset
  useEffect(() => {
    return () => {
      if (markerRef.current) {
        markerRef.current.remove();
        markerRef.current = null;
      }
      progressRef.current = 0;
    };
  }, []);

  return null;
}

// ── Golden Hour Timer Component ──────────────────────────────────
function GoldenHourTimer({ reportedTime }) {
  const [timeLeft, setTimeLeft] = useState('');
  const [urgency, setUrgency]   = useState('#00d4ff'); // default cyan
  const [blink, setBlink]       = useState(false);

  useEffect(() => {
    if (!reportedTime) return;
    const startTime = new Date(reportedTime).getTime();
    const goldenHourMs = 60 * 60 * 1000; // 60 minutes

    const interval = setInterval(() => {
      const now = new Date().getTime();
      const elapsed = now - startTime;
      const remaining = goldenHourMs - elapsed;

      if (remaining <= 0) {
        setTimeLeft('00:00');
        setUrgency('#ff3a5c');
        setBlink(true);
        clearInterval(interval);
        return;
      }

      const m = Math.floor(remaining / 60000);
      const s = Math.floor((remaining % 60000) / 1000);
      setTimeLeft(`${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`);

      if (m < 15) { setUrgency('#ffaa00'); setBlink(false); } // Amber under 15m
      if (m < 5)  { setUrgency('#ff3a5c'); setBlink(true); }  // Red/Blink under 5m
    }, 1000);

    return () => clearInterval(interval);
  }, [reportedTime]);

  if (!timeLeft) return null;

  return (
    <span style={{ 
      marginLeft: '8px', padding: '2px 6px', borderRadius: '4px', 
      fontSize: '10px', fontFamily: 'monospace', fontWeight: 'bold',
      color: urgency, border: `1px solid ${urgency}`,
      animation: blink ? 'pulse 1s infinite' : 'none',
      boxShadow: blink ? `0 0 8px ${urgency}66` : 'none'
    }}>
      <style>{`@keyframes pulse { 0% { opacity: 1; } 50% { opacity: 0.4; } 100% { opacity: 1; } }`}</style>
      ⏱ {timeLeft}
    </span>
  );
}

// ── Full Lifecycle Tracker Component ──────────────────────────────
function StatusTimeline({ status }) {
  // The 4 major phases of our emergency response
  const steps = ['Dispatched', 'On Scene', 'Transporting', 'Returning'];
  
  // Find where we are in the process
  let currentIndex = steps.indexOf(status);
  if (currentIndex === -1) currentIndex = 0; // Fallback to start

  return (
    <div style={{ position: 'relative', margin: '15px 0 10px 0', padding: '0 10px' }}>
      {/* The background connecting line */}
      <div style={{ position: 'absolute', top: '4px', left: '10%', right: '10%', height: '2px', background: '#333', zIndex: 0 }} />
      
      <div style={{ display: 'flex', justifyContent: 'space-between', position: 'relative', zIndex: 1 }}>
        {steps.map((step, idx) => {
          const isCompleted = idx < currentIndex;
          const isActive = idx === currentIndex;
          
          // Determine colors based on status
          let dotColor = '#222'; // Pending (Dark grey)
          if (isCompleted) dotColor = '#4CAF50'; // Done (Green)
          if (isActive) dotColor = '#00d4ff'; // Current (Cyan)

          return (
            <div key={step} style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', width: '25%' }}>
              <div style={{ 
                width: '10px', height: '10px', borderRadius: '50%', 
                background: dotColor,
                boxShadow: isActive ? `0 0 10px ${dotColor}` : 'none',
                border: '2px solid #070b14', transition: 'all 0.3s ease'
              }} />
              <div style={{ 
                fontSize: '8px', marginTop: '6px', fontFamily: 'monospace', textAlign: 'center',
                color: (isActive || isCompleted) ? '#c8dff0' : '#555',
                fontWeight: isActive ? 'bold' : 'normal'
              }}>
                {step}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ── Safe polyline ────────────────────────────────────────────────
function SafePolyline({ detail, incId }) {
  if (!detail?.route_shape || !Array.isArray(detail.route_shape)) return null;
  const valid = detail.route_shape.filter(c => Array.isArray(c) && c.length === 2 && typeof c[0] === 'number');
  if (valid.length < 2) return null;
  const col = detail.is_road_route ? '#00d4ff' : '#ffaa00';
  return (
    <Polyline
      key={`${incId}-${detail.unit_id}`}
      positions={valid}
      pathOptions={{ color: col, weight: 2.5, opacity: 0.7, dashArray: detail.is_road_route ? null : '6 4' }}
    />
  );
}

// ── AI Calculations panel ────────────────────────────────────────
function CalcPanel({ selectedInc }) {
  const [calcData, setCalcData] = useState(null);
  const [open, setOpen]         = useState(true);

  useEffect(() => {
    if (!selectedInc) { setCalcData(null); return; }
    fetch(`${API}/calc/${selectedInc.incident_id}`)
      .then(r => r.json())
      .then(d => setCalcData(d.steps))
      .catch(() => setCalcData(null));
  }, [selectedInc]);

  return (
    <div className="calc-panel">
      <div className="calc-hdr" onClick={() => setOpen(o => !o)}>
        <div style={{ width: 7, height: 7, borderRadius: '50%', background: calcData?.length ? '#00d4ff' : '#5a7a90', flexShrink: 0 }} />
        <span className="calc-hdr-title">AI Dispatch Calculations</span>
        <span className="calc-togif (!placed && markerRef.current) {
        markerRef.current.setLatLng(route[route.length - 1]);
        markerRef.current.setIcon(makeVehicleIcon(detail.unit_type, 1.0));
      }

      if (progress < 1.0) {
        rafRef.current = requestAnimationFrame(animate);
      }gle">{open ? '▲' : '▼'}</span>
      </div>

      {open && (
        <div className="calc-body">
          {!selectedInc && (
            <div className="calc-empty">Select an incident to see<br/>the optimizer calculations</div>
          )}
          {selectedInc && !calcData && (
            <div className="calc-empty">Loading…</div>
          )}
          {selectedInc && calcData && (
            <div className="calc-inc">
              <div className="calc-inc-id">{selectedInc.incident_id}</div>
              <div className="calc-inc-title">{selectedInc.type}</div>

              <div className="calc-row">
                <span className="calc-key">AI priority score</span>
                <span className={`calc-val ${selectedInc.priority >= 80 ? 'red' : selectedInc.priority >= 60 ? 'amb' : 'grn'}`}>
                  {selectedInc.priority} / 100
                </span>
              </div>
              <div className="calc-row">
                <span className="calc-key">Status</span>
                <span className={`calc-val ${selectedInc.status === 'Dispatched' ? 'grn' : selectedInc.status === 'Resolved' ? '' : 'amb'}`}>
                  {selectedInc.status}
                </span>
              </div>
              <div className="calc-row">
                <span className="calc-key">Units needed</span>
                <span className="calc-val">
                  {Object.entries(selectedInc.required_units || {}).map(([k, v]) => `${v}× ${k}`).join(', ')}
                </span>
              </div>

              {calcData.map((step, i) => (
                <div key={i}>
                  <div className="calc-divider" />
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 6 }}>
                   <span className="calc-step-title">Unit type</span>
                   <span className={`unit-type-badge ut-${step.unit_type.toLowerCase()}`}>{step.unit_type}</span>
                   <span className="calc-key" style={{ fontSize: 9 }}>
                    need {step.needed} / avail {step.available}
                    {selectedInc.preferred_capabilities?.[step.unit_type] ? ` · PREFER: ${selectedInc.preferred_capabilities[step.unit_type]}` : ''}
                   </span>
                  </div>

                  {step.candidates.length > 0 && (
                    <>
                      <div style={{ display: 'grid', gridTemplateColumns: '60px 1fr 60px 20px', gap: 4, fontSize: 9, color: 'var(--muted)', marginBottom: 3, paddingBottom: 3, borderBottom: '1px solid var(--border2)' }}>
                        <span>Unit</span><span>Station</span><span style={{ textAlign: 'right' }}>Score</span><span />
                      </div>
                      {step.candidates.map((c, j) => (
                        <div key={j} className="cand-row">
                          <span className="cand-id" style={{ color: c.selected ? 'var(--cyan)' : 'var(--muted)' }}>{c.unit_id}</span>
                          <span className="cand-dist">{c.station.split(' ')[0]} · {c.dist_miles}mi</span>
                          <span className="cand-score" style={{ color: c.capability_note?.includes('Preferred') ? 'var(--green)' : c.capability_note?.includes('Mismatch') ? 'var(--amber)' : 'var(--muted)', width: 'auto', whiteSpace: 'nowrap' }}>
                           {c.cost_score} {c.capability_note}
                          </span>
                          <span className="cand-sel">
                            {c.selected ? <span className="sel-tick">✓</span> : <span className="sel-cross">·</span>}
                          </span>
                        </div>
                      ))}
                    </>
                  )}

                  <div className="solver-line">⚙ {step.solver}</div>

                  {step.selected.length > 0 && (
                    <div className="calc-row" style={{ marginTop: 5 }}>
                      <span className="calc-key">Dispatched</span>
                      <span className="calc-val grn">{step.selected.join(', ')}</span>
                    </div>
                  )}
                </div>
              ))}

              {selectedInc.dispatch_details?.length > 0 && (
                <>
                  <div className="calc-divider" />
                  <div className="calc-step-title">Route details</div>
                  {selectedInc.dispatch_details.map((d, i) => (
                    <div key={i} style={{ marginBottom: 5 }}>
                      <div className="calc-row">
                        <span className="calc-key">{d.unit_id}</span>
                        <span className={`route-badge ${d.is_road_route ? 'route-road' : 'route-straight'}`}>
                          {d.is_road_route ? 'OSRM road' : 'straight-line'}
                        </span>
                      </div>
                      <div className="calc-row">
                        <span className="calc-key">distance</span>
                        <span className="calc-val">{d.distance} mi</span>
                      </div>
                      {d.duration_s && (
                        <div className="calc-row">
                          <span className="calc-key">est. travel</span>
                          <span className="calc-val grn">{Math.round(d.duration_s / 60)} min</span>
                        </div>
                      )}
                      <div className="calc-row">
                        <span className="calc-key">waypoints</span>
                        <span className="calc-val">{d.route_shape?.length || 0} pts</span>
                      </div>
                    </div>
                  ))}
                </>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ── Demand Forecast Panel ────────────────────────────────────────
function ForecastPanel() {
  const [data, setData] = useState([]);
  const [open, setOpen] = useState(true);

  useEffect(() => {
    const fetchForecast = () => {
      fetch(`${API}/forecast`)
        .then(r => r.json())
        .then(d => setData(d.forecast))
        .catch(() => {});
    };
    fetchForecast();
    const interval = setInterval(fetchForecast, 15000); // refresh every 15s
    return () => clearInterval(interval);
  }, []);

  return (
    <div className="forecast-panel" style={{ position: 'absolute', bottom: 20, right: 20, width: 350, background: '#070b14', border: '1px solid #1a273a', borderRadius: 8, zIndex: 1000, boxShadow: '0 4px 20px rgba(0,0,0,0.5)' }}>
      <div className="calc-hdr" onClick={() => setOpen(o => !o)} style={{ cursor: 'pointer', padding: '8px 12px', borderBottom: open ? '1px solid #1a273a' : 'none', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <div style={{ width: 7, height: 7, borderRadius: '50%', background: '#ffaa00', flexShrink: 0, boxShadow: '0 0 8px #ffaa00' }} />
          <span style={{ fontSize: 11, fontWeight: 700, color: '#c8dff0', letterSpacing: '1px' }}>AI DEMAND FORECAST (90 MIN)</span>
        </div>
        <span style={{ color: '#5a7a90', fontSize: 10 }}>{open ? '▼' : '▲'}</span>
      </div>

      {open && (
        <div style={{ padding: 12, height: 180 }}>
          {data.length === 0 ? (
             <div style={{ color: '#5a7a90', fontSize: 10, textAlign: 'center', marginTop: 60 }}>Loading forecast model...</div>
          ) : (
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={data} margin={{ top: 10, right: 0, left: -25, bottom: 0 }}>
                <defs>
                  <linearGradient id="colorEng" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#ff8c42" stopOpacity={0.3}/>
                    <stop offset="95%" stopColor="#ff8c42" stopOpacity={0}/>
                  </linearGradient>
                  <linearGradient id="colorAmb" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#00d4ff" stopOpacity={0.3}/>
                    <stop offset="95%" stopColor="#00d4ff" stopOpacity={0}/>
                  </linearGradient>
                </defs>
                <XAxis dataKey="time" stroke="#5a7a90" fontSize={9} tickLine={false} axisLine={false} />
                <YAxis stroke="#5a7a90" fontSize={9} tickLine={false} axisLine={false} />
                <Tooltip contentStyle={{ background: '#0d1421', border: '1px solid #1a273a', fontSize: 10, borderRadius: 4, color: '#c8dff0' }} itemStyle={{ fontSize: 10 }} />
                <Area type="monotone" dataKey="Engines" stroke="#ff8c42" fillOpacity={1} fill="url(#colorEng)" strokeWidth={2} />
                <Area type="monotone" dataKey="Ambulances" stroke="#00d4ff" fillOpacity={1} fill="url(#colorAmb)" strokeWidth={2} />
              </AreaChart>
            </ResponsiveContainer>
          )}
        </div>
      )}
    </div>
  );
}

// ── Audit Log Modal ──────────────────────────────────────────────
function AuditModal({ onClose }) {
  const [logs, setLogs] = useState([]);

  useEffect(() => {
    fetch(`${API}/audit-log`)
      .then(r => r.json())
      .then(d => setLogs(d.log))
      .catch(()=>{});
  }, []);

  const avgTime = logs.length 
    ? (logs.reduce((acc, l) => acc + l.duration_mins, 0) / logs.length).toFixed(1) 
    : 0;

  return (
    <div style={{ position: 'absolute', inset: 0, background: 'rgba(7,11,20,0.85)', zIndex: 9999, display: 'flex', alignItems: 'center', justifyContent: 'center', backdropFilter: 'blur(4px)' }}>
      <div style={{ width: 650, background: '#0d1421', border: '1px solid #00d4ff', borderRadius: 8, padding: 24, boxShadow: '0 0 30px rgba(0,212,255,0.15)' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', borderBottom: '1px solid #1a273a', paddingBottom: 12, marginBottom: 20 }}>
          <h2 style={{ color: '#00d4ff', margin: 0, fontSize: 16, fontFamily: 'monospace', letterSpacing: 1 }}>POST-INCIDENT AUDIT LOG</h2>
          <button onClick={onClose} style={{ background: 'transparent', border: 'none', color: '#ff3a5c', cursor: 'pointer', fontWeight: 'bold', fontFamily: 'monospace' }}>[X] CLOSE</button>
        </div>

        <div style={{ display: 'flex', gap: 20, marginBottom: 20 }}>
          <div style={{ background: '#111827', padding: '12px 20px', borderRadius: 6, border: '1px solid #1a273a', flex: 1 }}>
            <div style={{ fontSize: 10, color: '#5a7a90', marginBottom: 4, fontFamily: 'monospace' }}>TOTAL RESOLVED</div>
            <div style={{ fontSize: 24, color: '#00e87a', fontWeight: 'bold', fontFamily: 'monospace' }}>{logs.length}</div>
          </div>
          <div style={{ background: '#111827', padding: '12px 20px', borderRadius: 6, border: '1px solid #1a273a', flex: 1 }}>
            <div style={{ fontSize: 10, color: '#5a7a90', marginBottom: 4, fontFamily: 'monospace' }}>AVG CLEARANCE TIME</div>
            <div style={{ fontSize: 24, color: '#00d4ff', fontWeight: 'bold', fontFamily: 'monospace' }}>{avgTime} min</div>
          </div>
        </div>

        <div style={{ maxHeight: 350, overflowY: 'auto' }}>
          {logs.length === 0 ? <div style={{ color: '#5a7a90', textAlign: 'center', padding: 40, fontFamily: 'monospace' }}>No incidents resolved yet.</div> : (
            <table style={{ width: '100%', textAlign: 'left', fontSize: 12, borderCollapse: 'collapse', fontFamily: 'monospace' }}>
              <thead>
                <tr style={{ color: '#5a7a90', borderBottom: '1px solid #1a273a' }}>
                  <th style={{ padding: 10 }}>INCIDENT ID</th>
                  <th style={{ padding: 10 }}>TYPE</th>
                  <th style={{ padding: 10 }}>UNITS DEPLOYED</th>
                  <th style={{ padding: 10 }}>TIME TO RESOLVE</th>
                </tr>
              </thead>
              <tbody>
                {logs.map((l, i) => (
                  <tr key={i} style={{ borderBottom: '1px solid rgba(26,39,58,0.5)', color: '#c8dff0' }}>
                    <td style={{ padding: 10 }}>{l.incident_id}</td>
                    <td style={{ padding: 10 }}>{l.type} <span style={{color: l.priority >= 80 ? '#ff3a5c' : '#ffaa00'}}>(P{l.priority >= 80 ? '1' : l.priority >= 60 ? '2' : '3'})</span></td>
                    <td style={{ padding: 10, color: '#5a7a90' }}>{l.units.join(', ')}</td>
                    <td style={{ padding: 10, color: '#00e87a' }}>{l.duration_mins} min</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </div>
  );
}

// ── Main App ─────────────────────────────────────────────────────

// ── Map controller — flies to selected incident ──────────────────
function MapController({ selected }) {
  const map = useMap();
  useEffect(() => {
    if (selected?.location) {
      map.flyTo(
        [selected.location.lat, selected.location.lng],
        15,
        { animate: true, duration: 1.2 }
      );
    }
  }, [selected, map]);
  return null;
}

// ── Main App ─────────────────────────────────────────────────────

export default function App() {
  const [incidents, setIncidents] = useState([]);
  const [stations,  setStations]  = useState([]);
  const [selected,  setSelected]  = useState(null);
  const [showAudit, setShowAudit] = useState(false); // <--- NEW STATE

  const fetchData = useCallback(() => {
    fetch(`${API}/system-status`)
      .then(r => r.json())
      .then(d => {
        setIncidents(d.active_incidents || []);
        setStations(d.stations || []);
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    fetchData();
    let ws;
    try {
      ws = new WebSocket(`ws://127.0.0.1:8000/ws`);
      ws.onmessage = (e) => {
        const msg = JSON.parse(e.data);
        if (['NEW_INCIDENT', 'INCIDENT_RESOLVED', 'STATUS_UPDATE'].includes(msg.type)) {
          fetchData();
        }
      };
    } catch (_) {}
    const poll = setInterval(fetchData, 5000);
    return () => { ws?.close(); clearInterval(poll); };
  }, [fetchData]);

  const handleResolve = (incident_id, e) => {
    e.stopPropagation();
    fetch(`${API}/incidents/${incident_id}/resolve`, { method: 'POST' })
      .then(() => {
        if (selected?.incident_id === incident_id) setSelected(null);
        fetchData();
      });
  };

  const active   = incidents.filter(i => i.status !== 'Resolved');
  const resolved = incidents.filter(i => i.status === 'Resolved');

  const stats = {
    active:     active.length,
    dispatched: active.filter(i => i.status === 'Dispatched').length,
    resolved:   resolved.length,
    total:      incidents.length,
  };

  return (
    <div id="root">
      {/* --- NEW MODAL RENDER --- */}
      {showAudit && <AuditModal onClose={() => setShowAudit(false)} />}

      {/* ── TOPBAR ── */}
      <div className="topbar">
        <div>
          <div className="logo">Eras</div>
          <div className="logo-sub">Emergency Dispatch · Jaipur Grid</div>
        </div>
        <div className="live-indicator">
          <div className="live-dot" />
          LIVE
        </div>

        <div className="stat-row">
          <div className={`stat-chip ${stats.active > 0 ? 'alert' : ''}`}>
            ACTIVE <span className="val">{stats.active}</span>
          </div>
          <div className="stat-chip">
            DISPATCHED <span className="val">{stats.dispatched}</span>
          </div>
          <div className="stat-chip">
            RESOLVED <span className="val">{stats.resolved}</span>
          </div>
          <div className="stat-chip">
            TOTAL <span className="val">{stats.total}</span>
          </div>
        </div>

        {/* --- NEW BUTTON HERE --- */}
        <button 
          onClick={() => setShowAudit(true)} 
          style={{ background: '#111827', border: '1px solid #00d4ff', color: '#00d4ff', padding: '6px 14px', borderRadius: 4, cursor: 'pointer', fontFamily: 'monospace', fontWeight: 'bold', fontSize: 11, letterSpacing: 1 }}
        >
          📄 SYSTEM AUDIT LOG
        </button>
      </div>

      {/* ── MAIN ── */}
      <div className="layout">

        {/* ── SIDEBAR ── */}
        <div className="sidebar">
          <div className="sidebar-hdr">
            <span style={{ width: 6, height: 6, borderRadius: '50%', background: active.length ? '#ff3a5c' : '#5a7a90' }} />
            Incident Feed
            <span style={{ marginLeft: 'auto', color: 'var(--muted)', fontSize: 9 }}>{active.length} ACTIVE</span>
          </div>

          <div className="scroll">
            {active.length === 0 && (
              <div className="empty">
                No active incidents<br />
                <span style={{ fontSize: 10, marginTop: 6, display: 'block' }}>
                  Run python3 simulator.py<br />to generate test incidents
                </span>
              </div>
            )}

            {active.map(inc => (
              <div
                key={inc.incident_id}
                className={`inc-card ${selected?.incident_id === inc.incident_id ? 'active' : ''}`}
                onClick={() => setSelected(inc)}
              >
                <div className="inc-top">
                  <span className="inc-id">{inc.incident_id}</span>
                  <span className={`pri-badge ${inc.priority >= 80 ? 'p1' : inc.priority >= 60 ? 'p2' : 'p3'}`}>
                    P{inc.priority >= 80 ? '1' : inc.priority >= 60 ? '2' : '3'} · {inc.priority}
                  </span>
                  
                  {/* --- NEW: Golden Hour Timer for Critical Incidents --- */}
                  {inc.priority >= 80 && inc.status !== 'Resolved' && (
                    <GoldenHourTimer reportedTime={inc.reported_time} />
                  )}

                  {inc.dispatch_details?.some(d => d.is_road_route) && (
                    <span className="route-badge route-road" style={{marginLeft: 'auto'}}>ROAD</span>
                  )}
                  {inc.dispatch_details?.length > 0 && !inc.dispatch_details.some(d => d.is_road_route) && (
                    <span className="route-badge route-straight" style={{marginLeft: 'auto'}}>FALLBACK</span>
                  )}
                </div>

                <div className="inc-title">{inc.type}</div>
                <div className="inc-desc">
                  {inc.location?.lat?.toFixed(4)}, {inc.location?.lng?.toFixed(4)}
                </div>

                <div className="inc-tags">
                  {inc.assigned_units?.length > 0 && (
                    <span className="tag">{inc.assigned_units.length} unit{inc.assigned_units.length > 1 ? 's' : ''}</span>
                  )}
                  {inc.dispatch_details?.map(d => (
                    <span key={d.unit_id} className="tag">{d.unit_id} · {d.distance}mi</span>
                  ))}
                </div>

                {/* --- NEW: Visual Status Tracker --- */}
                {inc.status !== 'Resolved' && (
                  <StatusTimeline status={inc.status} />
                )}

                {/* --- DYNAMIC LIFECYCLE BUTTONS --- */}
                {inc.status === 'Dispatched' && (
                  <button className="res-btn" style={{ background: '#333', color: '#888', cursor: 'not-allowed' }} disabled>
                    Driving to Scene...
                  </button>
                )}
                
                {inc.status === 'On Scene' && (
                  <button className="res-btn" style={{ background: '#ffaa00', color: '#000' }} onClick={(e) => {
                    e.stopPropagation();
                    fetch(`${API}/incidents/${inc.incident_id}/transport`, { method: 'POST' });
                  }}>
                    🚑 Begin Transport / Return
                  </button>
                )}

                {/* NEW RETURN BUTTON */}
                {inc.status === 'Transporting' && (
                  <button className="res-btn" style={{ background: 'transparent', color: '#7b2fff', borderColor: '#7b2fff' }} onClick={(e) => {
                    e.stopPropagation();
                    fetch(`${API}/incidents/${inc.incident_id}/return`, { method: 'POST' });
                  }}>
                    🔄 Route Back to Station
                  </button>
                )}

                {/* NEW RETURNING STATE */}
                {inc.status === 'Returning' && (
                  <button className="res-btn" style={{ background: '#333', color: '#888', cursor: 'not-allowed' }} disabled>
                    Driving to Base... (Auto-Resolves)
                  </button>
                )}
              </div>
            ))}

            {resolved.length > 0 && (
              <>
                <div style={{ padding: '8px 14px', fontSize: 9, letterSpacing: '1.5px', color: 'var(--muted)', borderBottom: '1px solid var(--border2)', fontFamily: 'monospace' }}>
                  RESOLVED ({resolved.length})
                </div>
                {resolved.map(inc => (
                  <div key={inc.incident_id} className="inc-card" style={{ opacity: 0.4 }}>
                    <div className="inc-top">
                      <span className="inc-id">{inc.incident_id}</span>
                      <span className="tag" style={{ marginLeft: 'auto', color: 'var(--green)', borderColor: 'var(--green)' }}>✓ Resolved</span>
                    </div>
                    <div className="inc-title" style={{ fontSize: 12 }}>{inc.type}</div>
                  </div>
                ))}
              </>
            )}
          </div>
        </div>

        {/* ── MAP ── */}
        <div className="map-wrap">
          {active.length > 0 && (
            <div className="map-alert">
              <div className="alert-pill">
                ⚠ {active.length} ACTIVE INCIDENT{active.length > 1 ? 'S' : ''} — UNITS DEPLOYED
              </div>
            </div>
          )}

          <CalcPanel selectedInc={selected} />
          <ForecastPanel />
          <MapContainer
            center={[26.9124, 75.7873]}
            zoom={13}
            style={{ height: '100%', width: '100%' }}
            zoomControl={false}
          >
            <TileLayer
              url="https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png"
              attribution='© OpenStreetMap © CARTO'
              maxZoom={19}
            />
            <MapController selected={selected} />

            {/* Station markers */}
            {stations.map(s => (
              <Marker key={s.station_id} position={[s.location.lat, s.location.lng]} icon={makeStationIcon()}>
                <Popup>
                  <div style={{ fontFamily: 'monospace', fontSize: 12, background: '#070b14', color: '#c8dff0', padding: 4, minWidth: 160 }}>
                    <strong style={{ color: '#00d4ff' }}>{s.name}</strong><br />
                    ID: {s.station_id}<br />
                    Units: {s.units?.join(', ')}
                  </div>
                </Popup>
              </Marker>
            ))}

            {/* Incident markers + routes + moving vehicles */}
            {active.map(inc => (
              <>
                <Marker
                  key={`inc-${inc.incident_id}`}
                  position={[inc.location.lat, inc.location.lng]}
                  icon={makeIncidentIcon(
                    inc.priority >= 80 ? 'P1' : inc.priority >= 60 ? 'P2' : 'P3',
                    selected?.incident_id === inc.incident_id
                  )}
                >
                  <Popup>
                    <div style={{ fontFamily: 'monospace', fontSize: 11, background: '#070b14', color: '#c8dff0', padding: 4, minWidth: 170 }}>
                      <strong style={{ color: '#ff3a5c' }}>{inc.incident_id}</strong><br />
                      Type: {inc.type}<br />
                      Priority: {inc.priority}<br />
                      Status: {inc.status}<br />
                      Units: {inc.assigned_units?.join(', ') || 'None'}
                    </div>
                  </Popup>
                </Marker>

                {inc.dispatch_details?.map(detail => (
                  <div key={`wrap-${inc.incident_id}-${detail.unit_id}`}>
                    <SafePolyline
                      key={`route-${inc.incident_id}-${detail.unit_id}-${inc.status}`}
                      detail={detail}
                      incId={inc.incident_id}
                    />
                    {(inc.status === 'Dispatched' || inc.status === 'Returning') && (
                      <MovingVehicle
                        key={`veh-${inc.incident_id}-${detail.unit_id}-${inc.status}`}
                        detail={detail}
                        incidentId={inc.incident_id}
                        incidentStatus={inc.status}
                      />
                    )}
                  </div>
                ))}
              </>
            ))}
          </MapContainer>
        </div>
      </div>
    </div>
  );
}
