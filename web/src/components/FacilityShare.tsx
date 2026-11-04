import { useState } from 'react';
import './facility-share.css';

/** Stable, crawler-readable URLs only exist for EPA ECHO records. */
export function facilityShareUrl(id: string): string | null {
  const match = /^echo[:_]([a-z0-9]{1,30})(?:[-_](\d+))?$/i.exec(id);
  return match?.[1] ? `https://wattdocket.com/share/echo_${match[1].toLowerCase()}${match[2] ? `-${match[2]}` : ''}.html` : null;
}

export function FacilityShare({ facilityId, production = false }: { facilityId: string; production?: boolean }) {
  const url = production ? `https://wattdocket.com/app/index.html#/public/${encodeURIComponent(facilityId)}` : facilityShareUrl(facilityId);
  const [status, setStatus] = useState('');
  const [showUrl, setShowUrl] = useState(false);
  if (!url) return null;

  async function copy() {
    try {
      await navigator.clipboard.writeText(url!);
      setStatus('Public link copied.');
      setShowUrl(false);
    } catch {
      setShowUrl(true);
      setStatus('Copy the public link below.');
    }
  }

  const facebook = `https://www.facebook.com/sharer/sharer.php?u=${encodeURIComponent(url)}`;
  return <div className="facility-share">
    <div className="facility-share__actions">
      <button type="button" className="ghostbtn" onClick={() => void copy()}>Copy public link</button>
      {!production && <a className="ghostbtn" href={facebook} target="_blank" rel="noopener noreferrer">Share on Facebook <span aria-hidden="true">↗</span></a>}
    </div>
    <span className="facility-share__status" role="status">{status}</span>
    {showUrl && <label className="facility-share__fallback">Public link<input readOnly value={url} onFocus={(event) => event.currentTarget.select()} /></label>}
  </div>;
}
