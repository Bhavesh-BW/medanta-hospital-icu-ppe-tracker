import React, { useState, useEffect, useMemo } from 'react';
import { createRoot } from 'react-dom/client';
import './theme.css';

function formatTime(ts) {
  if (!ts) return '—';
  const d = new Date(ts);
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

export function App() {
  const [state, setState] = useState(null);
  const [analytics, setAnalytics] = useState(null);
  const [events, setEvents] = useState([]);
  const [timeRange, setTimeRange] = useState('today');
  const [statusFilter, setStatusFilter] = useState('');
  const [streamFilter, setStreamFilter] = useState('');
  const [viewMode, setViewMode] = useState('grid'); // 'grid' | 'focus'
  const [focusStream, setFocusStream] = useState('icu_8_in');
  const [modalImage, setModalImage] = useState(null);
  const [error, setError] = useState(null);

  // 3-slot fixed grid state (Slot 1, Slot 2, Slot 3)
  const [gridSlots, setGridSlots] = useState(() => {
    try {
      const saved = localStorage.getItem('ppe_grid_slots');
      if (saved) {
        const parsed = JSON.parse(saved);
        if (Array.isArray(parsed) && parsed.length === 3) return parsed;
      }
    } catch (e) {}
    return ['icu_8_backside', 'icu_8_in', 'icu_cori'];
  });

  const updateGridSlot = (slotIdx, streamKey) => {
    setGridSlots(prev => {
      const next = [...prev];
      next[slotIdx] = streamKey;
      try {
        localStorage.setItem('ppe_grid_slots', JSON.stringify(next));
      } catch (e) {}
      return next;
    });
  };

  // Simple Add/Edit Camera Modal (Only Name and URL, just like Counter)
  const [cameraModal, setCameraModal] = useState({
    open: false,
    mode: 'add', // 'add' | 'edit'
    id: '',
    name: '',
    url: '',
    targetSlot: 'auto',
  });
  const [isSubmittingCam, setIsSubmittingCam] = useState(false);

  // Rules form
  const [rulesForm, setRulesForm] = useState({
    required_ppe: ['mask', 'surgical_cap'],
    person_conf: 0.35,
    ppe_conf: 0.20,
    cooldown_s: 6,
  });

  const fetchData = async () => {
    try {
      setError(null);
      const [stateRes, analyticsRes, eventsRes] = await Promise.all([
        fetch('/api/state').then(r => (r.ok ? r.json() : null)),
        fetch(`/api/analytics?time_range=${timeRange}`).then(r => (r.ok ? r.json() : null)),
        fetch(`/api/events?time_range=${timeRange}${statusFilter ? `&status=${statusFilter}` : ''}${streamFilter ? `&stream=${streamFilter}` : ''}&limit=100`).then(r => (r.ok ? r.json() : { events: [] })),
      ]);

      if (stateRes) {
        setState(stateRes);
        if (stateRes.rules) {
          setRulesForm({
            required_ppe: stateRes.rules.required_ppe || ['mask', 'surgical_cap'],
            person_conf: stateRes.rules.person_conf ?? 0.35,
            ppe_conf: stateRes.rules.ppe_conf ?? 0.20,
            cooldown_s: stateRes.rules.cooldown_s ?? 6,
          });
        }
      }
      if (analyticsRes) setAnalytics(analyticsRes);
      if (eventsRes?.events) setEvents(eventsRes.events);
    } catch (err) {
      console.error('Fetch error:', err);
      setError('Cannot connect to PPE Tracker backend.');
    }
  };

  useEffect(() => {
    fetchData();
    const interval = setInterval(fetchData, 4000);
    return () => clearInterval(interval);
  }, [timeRange, statusFilter, streamFilter]);

  const handleTogglePpeItem = (item) => {
    const list = [...rulesForm.required_ppe];
    const idx = list.indexOf(item);
    if (idx >= 0) {
      if (list.length > 1) list.splice(idx, 1);
    } else {
      list.push(item);
    }
    setRulesForm({ ...rulesForm, required_ppe: list });
  };

  const handleSaveRules = async (e) => {
    e.preventDefault();
    try {
      const res = await fetch('/api/rules', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(rulesForm),
      });
      if (res.ok) {
        alert('PPE compliance rules saved successfully!');
        fetchData();
      }
    } catch (err) {
      alert('Error saving rules: ' + err.message);
    }
  };

  const devices = (state?.devices || [
    { stream: 'icu_8_backside', name: 'ICU 8 Backside', role: 'inside', active: true, url: 'rtsp://124.123.89.162:31554/live/icu_8_backside' },
    { stream: 'icu_8_in', name: 'ICU 8 Inside', role: 'inside', active: true, url: 'rtsp://124.123.89.162:31554/live/icu_8_in' },
    { stream: 'icu_cori', name: 'ICU 7 8 Corridor', role: 'corridor', active: true, url: 'rtsp://124.123.89.162:31554/live/icu_cori' },
  ]).filter(d => d.active !== false);

  const workers = state?.workers || {};
  const isRunning = Boolean(state?.running);
  const complianceRate = analytics?.compliance_rate ?? 100;

  // Helper to find a device by stream key, id, or url
  const findDevice = (streamKey) => {
    if (!streamKey) return null;
    return devices.find(d => d.stream === streamKey || d.id === streamKey || d.url === streamKey);
  };

  // Open modal in Add mode
  const openAddModal = (targetSlot = 'auto') => {
    setCameraModal({
      open: true,
      mode: 'add',
      id: '',
      name: '',
      url: '',
      targetSlot: String(targetSlot),
    });
  };

  // Open modal in Edit mode
  const openEditModal = (dev) => {
    setCameraModal({
      open: true,
      mode: 'edit',
      id: dev.stream || dev.id,
      name: dev.name || '',
      url: dev.url || dev.stream || '',
      targetSlot: 'auto',
    });
  };

  const closeCameraModal = () => {
    setCameraModal(prev => ({ ...prev, open: false }));
  };

  // Handle Save Camera (both Add and Edit)
  const handleSaveCamera = async (e) => {
    e.preventDefault();
    const name = cameraModal.name.trim();
    const url = cameraModal.url.trim();
    if (!name || !url) {
      alert('Please enter both Camera Name and RTSP Stream URL.');
      return;
    }

    setIsSubmittingCam(true);
    try {
      const payload = {
        name,
        url,
        stream: cameraModal.mode === 'edit' ? cameraModal.id : '',
      };

      const res = await fetch('/api/devices', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });

      if (res.ok) {
        const data = await res.json().catch(() => ({}));
        const streamKey = data.stream || cameraModal.id;

        if (cameraModal.mode === 'add') {
          if (cameraModal.targetSlot === 'auto') {
            const emptyIdx = gridSlots.findIndex((s, idx) => !findDevice(s));
            if (emptyIdx !== -1) {
              updateGridSlot(emptyIdx, streamKey);
            } else {
              updateGridSlot(2, streamKey);
            }
          } else {
            const slotIdx = parseInt(cameraModal.targetSlot, 10);
            if (!isNaN(slotIdx) && slotIdx >= 0 && slotIdx < 3) {
              updateGridSlot(slotIdx, streamKey);
            }
          }
        }

        closeCameraModal();
        await fetchData();
      } else {
        const data = await res.json().catch(() => ({}));
        alert('Failed to save camera: ' + (data.error || 'Server error'));
      }
    } catch (err) {
      alert('Network error while saving camera: ' + err.message);
    } finally {
      setIsSubmittingCam(false);
    }
  };

  const handleDeleteCamera = async (streamKey, name) => {
    if (!window.confirm(`Are you sure you want to remove "${name}" from registered cameras?`)) return;
    try {
      const res = await fetch('/api/devices/delete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ stream: streamKey }),
      });
      if (res.ok) {
        setGridSlots(prev => prev.map(s => (s === streamKey ? '' : s)));
        await fetchData();
      } else {
        alert('Failed to remove camera.');
      }
    } catch (err) {
      alert('Error removing camera: ' + err.message);
    }
  };

  return (
    <div className="shell">
      {/* Top Bar */}
      <header className="topbar">
        <div className="brand">
          <div className="brand-mark">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
              <path d="M9 12l2 2 4-4" />
            </svg>
          </div>
          <div>
            <h1>Medanta ICU · PPE Compliance Monitor</h1>
            <p>Live Multi-Camera Person Bounding Box & Protocol Enforcement</p>
          </div>
        </div>

        <div className="flex">
          <button
            className={`btn ${viewMode === 'grid' ? 'primary' : ''}`}
            onClick={() => setViewMode('grid')}
            title="Camera Grid View"
          >
            ⊞ Camera Grid
          </button>

          <button
            className={`btn ${viewMode === 'focus' ? 'primary' : ''}`}
            onClick={() => setViewMode('focus')}
            title="Single Camera Focus"
          >
            □ Focus Mode
          </button>

          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <span style={{ fontSize: 12, color: '#64748b', fontWeight: 600 }}>Period:</span>
            <select
              value={timeRange}
              onChange={(e) => setTimeRange(e.target.value)}
              style={{
                padding: '6px 10px',
                borderRadius: 8,
                border: '1px solid #cbd5e1',
                fontSize: 12,
                background: 'white',
                fontWeight: 600,
                color: '#1e293b',
                cursor: 'pointer'
              }}
              title="Filter by shift or historical evidence"
            >
              <option value="today">Today (Live Shift)</option>
              <option value="yesterday">Yesterday</option>
              <option value="3days">Last 3 Days</option>
              <option value="7days">Last 7 Days</option>
              <option value="all">All Time (Archive)</option>
            </select>
          </div>

          <button className="btn" onClick={fetchData} title="Refresh data">
            ↻
          </button>

          <button
            className="btn primary"
            onClick={() => openAddModal('auto')}
            style={{ fontWeight: 800 }}
            title="Add a new camera"
          >
            + Add Camera
          </button>

          <div className={`status-pill ${isRunning ? 'live' : 'down'}`}>
            <span className="dot" />
            {isRunning ? `Inference Live (${state?.device || 'CPU'})` : 'Engine Offline'}
          </div>
        </div>
      </header>

      {error && <div className="notice err">{error}</div>}

      {/* Real-Time KPIs */}
      <section className="kpis">
        <div className="kpi" style={{ '--kpi': 'var(--teal-500)' }}>
          <span>ICU Compliance Rate</span>
          <b style={{ color: complianceRate < 80 ? 'var(--red-500)' : 'var(--teal-700)' }}>
            {complianceRate}%
          </b>
          <small>Mask + surgical cap compliant</small>
        </div>

        <div className="kpi" style={{ '--kpi': 'var(--green-500)' }}>
          <span>Compliant Persons</span>
          <b style={{ color: '#15803d' }}>{analytics?.compliant_count ?? 0}</b>
          <small>Passed all required PPE items</small>
        </div>

        <div className="kpi" style={{ '--kpi': 'var(--red-500)' }}>
          <span>Violations Logged</span>
          <b style={{ color: 'var(--red-500)' }}>{analytics?.violation_count ?? 0}</b>
          <small>Missing mask or surgical cap</small>
        </div>

        <div className="kpi" style={{ '--kpi': '#64748b' }}>
          <span>Total Checks</span>
          <b>{analytics?.total_checks ?? 0}</b>
          <small>Monitored persons in window</small>
        </div>
      </section>

      {/* Main Grid */}
      <div className="grid">
        {/* Live Camera Section */}
        <div className="card span12">
          <div className="head">
            <div className="flex">
              <h3>Live ICU Camera Feeds (3-Slot Grid)</h3>
              <span className="badge compliant">● Green: Compliant</span>
              <span className="badge violation">● Red: Violation</span>
            </div>

            <div className="flex">
              {viewMode === 'focus' ? (
                <div className="flex">
                  <span style={{ fontSize: 11, fontWeight: 700 }}>Select Camera:</span>
                  <select value={focusStream} onChange={e => setFocusStream(e.target.value)}>
                    {devices.map(d => (
                      <option key={d.stream} value={d.stream}>
                        {d.name} ({d.stream})
                      </option>
                    ))}
                  </select>
                </div>
              ) : (
                <button
                  className="btn primary sm"
                  onClick={() => openAddModal('auto')}
                >
                  + Add Camera
                </button>
              )}
            </div>
          </div>

          <div className="body" style={{ background: '#0b1329', padding: 14 }}>
            {viewMode === 'grid' ? (
              /* Fixed 3-Slot Grid Layout */
              <div className="cam-grid">
                {[0, 1, 2].map(slotIdx => {
                  const currentStream = gridSlots[slotIdx];
                  const currentDev = findDevice(currentStream) || (devices[slotIdx] ? devices[slotIdx] : null);
                  const worker = currentDev ? workers[currentDev.stream] : null;

                  if (!currentDev) {
                    /* Empty Slot Placeholder */
                    return (
                      <div key={`empty-slot-${slotIdx}`} className="cam-tile empty-slot">
                        <div className="empty-slot-content">
                          <div className="empty-slot-icon">📹</div>
                          <h4>Slot {slotIdx + 1} — Available</h4>
                          <p>Select a camera to view in this slot, or add a new camera stream.</p>
                          <div className="empty-slot-actions">
                            <select
                              onChange={e => updateGridSlot(slotIdx, e.target.value)}
                              defaultValue=""
                            >
                              <option value="" disabled>Choose Camera to Display...</option>
                              {devices.map(dev => (
                                <option key={dev.stream} value={dev.stream}>
                                  {dev.name}
                                </option>
                              ))}
                            </select>
                            <button
                              className="btn sm primary"
                              onClick={() => openAddModal(slotIdx)}
                            >
                              + Add New Camera
                            </button>
                          </div>
                        </div>
                      </div>
                    );
                  }

                  return (
                    <div
                      key={`slot-${slotIdx}-${currentDev.stream}`}
                      className="cam-tile active"
                      style={{ cursor: 'pointer' }}
                      title="Click video to zoom in Focus Mode"
                    >
                      <div className="cam-tile-head">
                        <div style={{ display: 'flex', alignItems: 'center', gap: 6, overflow: 'hidden' }}>
                          <span className="slot-badge">Slot {slotIdx + 1}</span>
                          <span style={{ whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                            📹 {currentDev.name}
                          </span>
                        </div>
                        <div className="flex" style={{ gap: 6 }}>
                          {/* Quick Edit Camera URL & Name Button */}
                          <button
                            className="btn sm"
                            onClick={(e) => {
                              e.stopPropagation();
                              openEditModal(currentDev);
                            }}
                            title={`Edit ${currentDev.name} URL`}
                            style={{
                              padding: '2px 7px',
                              fontSize: 10,
                              background: '#334155',
                              color: '#f8fafc',
                              borderColor: '#475569',
                              fontWeight: 700
                            }}
                          >
                            ✏️ Edit
                          </button>

                          {/* Inline Slot Camera Replacer */}
                          <select
                            className="slot-selector"
                            value={currentDev.stream}
                            onChange={e => updateGridSlot(slotIdx, e.target.value)}
                            title="Replace camera in this slot"
                            onClick={e => e.stopPropagation()}
                          >
                            <option disabled value="">Switch Camera...</option>
                            {devices.map(dev => (
                              <option key={dev.stream} value={dev.stream}>
                                {dev.name} {gridSlots.includes(dev.stream) && dev.stream !== currentDev.stream ? '● In Grid' : ''}
                              </option>
                            ))}
                          </select>

                          {worker?.fps ? (
                            <span style={{ color: '#4ade80', fontSize: 10, whiteSpace: 'nowrap' }}>
                              {worker.fps} FPS
                            </span>
                          ) : null}

                          <span className={`badge ${worker?.violations ? 'violation' : 'compliant'}`}>
                            {worker?.violations ? `${worker.violations} Violation` : 'All Compliant'}
                          </span>
                        </div>
                      </div>

                      <div
                        className="cam-stage"
                        onClick={() => {
                          setFocusStream(currentDev.stream);
                          setViewMode('focus');
                        }}
                      >
                        <img
                          src={`/api/devices/${currentDev.stream}/stream.mjpg`}
                          alt={currentDev.name}
                          onError={e => {
                            e.target.style.display = 'none';
                            if (e.target.nextSibling) e.target.nextSibling.style.display = 'block';
                          }}
                        />
                        <div className="cam-empty" style={{ display: 'none' }}>
                          Connecting to {currentDev.name} stream...
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>
            ) : (
              /* Focus Mode */
              <div style={{ maxWidth: 1080, margin: '0 auto' }}>
                {(() => {
                  const currentDev = findDevice(focusStream) || devices[0];
                  if (!currentDev) return <div className="cam-empty">No active camera selected.</div>;
                  const worker = workers[currentDev.stream];
                  return (
                    <div className="cam-tile active">
                      <div className="cam-tile-head">
                        <span>📹 {currentDev.name} · {currentDev.url || currentDev.stream}</span>
                        <div className="flex">
                          <button
                            className="btn sm"
                            onClick={() => openEditModal(currentDev)}
                            style={{ padding: '2px 8px', fontSize: 10 }}
                          >
                            ✏️ Edit URL
                          </button>
                          <span style={{ color: '#4ade80', fontSize: 11 }}>
                            {worker?.fps || 0} FPS · {worker?.scan_ms || 0}ms scan
                          </span>
                          <span className={`badge ${worker?.violations ? 'violation' : 'compliant'}`}>
                            {worker?.persons || 0} Persons · {worker?.violations || 0} Violations
                          </span>
                        </div>
                      </div>
                      <div className="cam-stage" style={{ minHeight: 480 }}>
                        <img
                          src={`/api/devices/${currentDev.stream}/stream.mjpg`}
                          alt={currentDev.name}
                        />
                      </div>
                    </div>
                  );
                })()}
              </div>
            )}
          </div>
        </div>

        {/* Audit Log Table */}
        <div className="card span8">
          <div className="head">
            <div>
              <h3>PPE Compliance & Violation Log</h3>
              <small>Real-time person checks with snapshot evidence</small>
            </div>

            <div className="flex">
              <select value={statusFilter} onChange={e => setStatusFilter(e.target.value)}>
                <option value="">All Events</option>
                <option value="violation">Violations Only</option>
                <option value="compliant">Compliant Only</option>
              </select>

              <select value={streamFilter} onChange={e => setStreamFilter(e.target.value)}>
                <option value="">All Cameras</option>
                {devices.map(d => (
                  <option key={d.stream} value={d.stream}>
                    {d.name}
                  </option>
                ))}
              </select>
            </div>
          </div>

          <div style={{ overflowX: 'auto' }}>
            <table className="tbl">
              <thead>
                <tr>
                  <th>Timestamp</th>
                  <th>Camera</th>
                  <th>Status</th>
                  <th>Missing PPE</th>
                  <th>Detected Items</th>
                  <th>Person Snapshot</th>
                </tr>
              </thead>
              <tbody>
                {events.length === 0 ? (
                  <tr>
                    <td colSpan="6" className="empty">
                      No events logged matching current filter.
                    </td>
                  </tr>
                ) : (
                  events.slice(0, 40).map(ev => (
                    <tr key={ev.id}>
                      <td>
                        <b style={{ color: '#1e293b', whiteSpace: 'nowrap' }}>
                          {timeRange !== 'today' && ev.date ? `${ev.date} ` : ''}{formatTime(ev.ts)}
                        </b>
                      </td>
                      <td>
                        <span style={{ fontWeight: 600 }}>{ev.camera_name}</span>
                      </td>
                      <td>
                        <span className={`badge ${ev.status}`}>
                          {ev.status === 'compliant' ? '✓ COMPLIANT' : '⚠ VIOLATION'}
                        </span>
                      </td>
                      <td>
                        <div className="chips">
                          {ev.missing && ev.missing.length > 0 ? (
                            ev.missing.map((m, idx) => (
                              <span key={idx} className="chip missing">
                                No {m.replace('_', ' ')}
                              </span>
                            ))
                          ) : (
                            <span style={{ color: '#15803d', fontSize: 11, fontWeight: 700 }}>None</span>
                          )}
                        </div>
                      </td>
                      <td>
                        <div className="chips">
                          {ev.detected && ev.detected.length > 0 ? (
                            ev.detected.map((d, idx) => (
                              <span key={idx} className="chip found">
                                {d.replace('_', ' ')}
                              </span>
                            ))
                          ) : (
                            <span style={{ color: '#94a3b8', fontSize: 11 }}>No items</span>
                          )}
                        </div>
                      </td>
                      <td>
                        {ev.person_url ? (
                          <img
                            className="thumb-person"
                            src={`/api${ev.person_url}`}
                            alt="Person crop"
                            onClick={() => setModalImage(`/api${ev.photo_url || ev.person_url}`)}
                            title="Click to view full camera snapshot"
                          />
                        ) : (
                          <span style={{ color: '#94a3b8' }}>—</span>
                        )}
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>

        {/* Rule Configurator & Registered Cameras */}
        <div className="card span4">
          <div className="head">
            <h3>Protocol Configuration</h3>
            <small>Tuning required PPE & detection sensitivity</small>
          </div>

          <div className="body">
            <form onSubmit={handleSaveRules} className="field" style={{ gap: 14 }}>
              <div>
                <span style={{ fontSize: 10, fontWeight: 800, textTransform: 'uppercase', color: '#64748b' }}>
                  Required PPE Checklist
                </span>
                <div style={{ display: 'grid', gap: 8, marginTop: 8 }}>
                  <label className="check">
                    <input
                      type="checkbox"
                      checked={rulesForm.required_ppe.includes('mask')}
                      onChange={() => handleTogglePpeItem('mask')}
                    />
                    <span>Medical Mask (Mandatory)</span>
                  </label>

                  <label className="check">
                    <input
                      type="checkbox"
                      checked={rulesForm.required_ppe.includes('surgical_cap')}
                      onChange={() => handleTogglePpeItem('surgical_cap')}
                    />
                    <span>Surgical Cap / Hairnet (Mandatory)</span>
                  </label>

                  <label className="check">
                    <input
                      type="checkbox"
                      checked={rulesForm.required_ppe.includes('gloves')}
                      onChange={() => handleTogglePpeItem('gloves')}
                    />
                    <span>Medical Gloves</span>
                  </label>

                  <label className="check">
                    <input
                      type="checkbox"
                      checked={rulesForm.required_ppe.includes('surgical_gown')}
                      onChange={() => handleTogglePpeItem('surgical_gown')}
                    />
                    <span>Surgical Gown / Apron</span>
                  </label>
                </div>
              </div>

              <div className="field">
                <span>Person Confidence Threshold ({Math.round(rulesForm.person_conf * 100)}%)</span>
                <input
                  type="range"
                  min="0.15"
                  max="0.80"
                  step="0.05"
                  value={rulesForm.person_conf}
                  onChange={e => setRulesForm({ ...rulesForm, person_conf: parseFloat(e.target.value) })}
                />
              </div>

              <div className="field">
                <span>PPE Item Confidence Threshold ({Math.round(rulesForm.ppe_conf * 100)}%)</span>
                <input
                  type="range"
                  min="0.10"
                  max="0.70"
                  step="0.05"
                  value={rulesForm.ppe_conf}
                  onChange={e => setRulesForm({ ...rulesForm, ppe_conf: parseFloat(e.target.value) })}
                />
              </div>

              <div className="field">
                <span>Audit Cooldown (seconds per person)</span>
                <input
                  type="number"
                  min="1"
                  max="60"
                  value={rulesForm.cooldown_s}
                  onChange={e => setRulesForm({ ...rulesForm, cooldown_s: parseInt(e.target.value) })}
                />
              </div>

              <button type="submit" className="btn primary" style={{ width: '100%', marginTop: 6 }}>
                Update Protocol Rules
              </button>
            </form>

            <hr style={{ border: 0, borderTop: '1px solid var(--slate-100)', margin: '20px 0' }} />

            {/* Registered Cameras Pool & Slot Assigner */}
            <div>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 }}>
                <h4 style={{ margin: 0, fontSize: 12, fontWeight: 800 }}>
                  Registered ICU Cameras ({devices.length})
                </h4>
                <button
                  className="btn sm"
                  onClick={() => openAddModal('auto')}
                  title="Add new camera"
                >
                  + Add
                </button>
              </div>

              <div style={{ display: 'grid', gap: 8 }}>
                {devices.map(d => {
                  const slotIndex = gridSlots.indexOf(d.stream);
                  return (
                    <div
                      key={d.stream}
                      style={{
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'space-between',
                        padding: '10px 12px',
                        background: 'var(--slate-50)',
                        borderRadius: 10,
                        border: '1px solid var(--slate-200)',
                        gap: 10,
                      }}
                    >
                      <div style={{ minWidth: 0, flex: 1 }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                          <b style={{ fontSize: 12, color: '#1e293b', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                            {d.name}
                          </b>
                          {slotIndex !== -1 ? (
                            <span className="slot-badge">Slot {slotIndex + 1}</span>
                          ) : (
                            <span className="badge" style={{ background: '#f1f5f9', color: '#64748b' }}>
                              In Pool
                            </span>
                          )}
                        </div>
                        <small style={{ color: '#64748b', display: 'block', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', fontSize: 10 }}>
                          {d.url || d.stream}
                        </small>
                      </div>

                      <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                        <button
                          className="btn sm"
                          onClick={() => openEditModal(d)}
                          title="Edit camera name & stream URL"
                          style={{ padding: '3px 7px', fontSize: 10 }}
                        >
                          ✏️ Edit
                        </button>

                        <select
                          className="slot-selector"
                          style={{ fontSize: 10, padding: '3px 6px', maxWidth: 90 }}
                          value={slotIndex !== -1 ? String(slotIndex) : ''}
                          onChange={e => updateGridSlot(parseInt(e.target.value, 10), d.stream)}
                          title="Assign to grid slot"
                        >
                          <option value="" disabled>Show in...</option>
                          <option value="0">Slot 1</option>
                          <option value="1">Slot 2</option>
                          <option value="2">Slot 3</option>
                        </select>

                        <button
                          className="btn sm danger"
                          onClick={() => handleDeleteCamera(d.stream, d.name)}
                          title="Remove camera from pool"
                          style={{ padding: '3px 7px', fontSize: 11 }}
                        >
                          ✕
                        </button>
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* Simple Add / Edit Camera Modal (Only Name and URL, just like Counter) */}
      {cameraModal.open && (
        <div className="modal-overlay" onClick={closeCameraModal}>
          <div className="modal-card" onClick={e => e.stopPropagation()}>
            <div className="modal-card-head">
              <div>
                <h3>{cameraModal.mode === 'edit' ? 'Edit Camera' : 'Add New Camera'}</h3>
                <small>
                  {cameraModal.mode === 'edit'
                    ? `Update camera name and RTSP stream URL`
                    : 'Add camera with name and RTSP stream URL'}
                </small>
              </div>
              <button
                className="btn sm"
                onClick={closeCameraModal}
                style={{ border: 'none', background: 'transparent', fontSize: 16, cursor: 'pointer' }}
              >
                ✕
              </button>
            </div>

            <form onSubmit={handleSaveCamera}>
              <div className="modal-card-body">
                <div className="field">
                  <span>Camera Name *</span>
                  <input
                    type="text"
                    required
                    placeholder="e.g. ICU 7 8 Corridor"
                    value={cameraModal.name}
                    onChange={e => setCameraModal({ ...cameraModal, name: e.target.value })}
                  />
                </div>

                <div className="field">
                  <span>RTSP Stream URL *</span>
                  <input
                    type="text"
                    required
                    placeholder="e.g. rtsp://124.123.89.162:31554/live/icu_cori"
                    value={cameraModal.url}
                    onChange={e => setCameraModal({ ...cameraModal, url: e.target.value })}
                  />
                  <small style={{ color: '#64748b', fontSize: 10 }}>
                    Enter the RTSP camera stream URL or stream key.
                  </small>
                </div>
              </div>

              <div className="modal-card-foot">
                <button
                  type="button"
                  className="btn"
                  onClick={closeCameraModal}
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  className="btn primary"
                  disabled={isSubmittingCam}
                >
                  {isSubmittingCam ? 'Saving...' : (cameraModal.mode === 'edit' ? 'Save Changes' : 'Add Camera')}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Snapshot Modal */}
      {modalImage && (
        <div className="modal-overlay" onClick={() => setModalImage(null)}>
          <div className="modal-content" onClick={e => e.stopPropagation()}>
            <img src={modalImage} alt="Full Frame Snapshot" />
            <div className="modal-foot">
              <span style={{ fontWeight: 700, fontSize: 12 }}>Camera Snapshot with HUD Detections</span>
              <button className="btn sm" onClick={() => setModalImage(null)}>
                Close
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

const root = createRoot(document.getElementById('root'));
root.render(<App />);
