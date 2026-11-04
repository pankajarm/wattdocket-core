import { operatorHref, publicHref } from '../router';
import './site-footer.css';

export function SiteFooter({ facilityId }: {facilityId?: string | null}) {
  return <footer className="site-footer" aria-label="Site information">
    <div className="site-footer__about"><a className="brand" href="#/">watt<b>docket</b></a><p>Public evidence for understanding data centers.</p><p>Sources, reporting years and the limits of each claim.</p></div>
    <nav aria-label="Explore WattDocket"><a href="#/reported">Reported data</a><a href="#/map">Records map</a><a href="#/address">Address lookup</a><a href="#/grid">Regional grid</a></nav>
    <nav aria-label="Understand the evidence"><a href="#/learn">Questions explained</a><a href="#/data">Coverage &amp; sources</a><a href="#/learn/virginia">Virginia research guide</a>{facilityId && <><a href={publicHref(facilityId)}>Selected public record</a><a href={operatorHref(facilityId)}>Operator workspace</a></>}</nav>
    <div className="site-footer__bottom"><span>Missing evidence is not zero. A source record is not a verified site.</span><nav aria-label="Privacy and terms"><a href="/privacy.html">Privacy</a><a href="/terms.html">Terms</a><a href="/data-deletion.html">Account deletion</a></nav></div>
  </footer>;
}
