import './site-footer.css';

export function SiteFooter() {
  return <footer className="site-footer" aria-label="Site information">
    <span>WattDocket · Public records, in perspective.</span>
    <nav aria-label="Privacy and terms">
      <a href="#/data">Data coverage</a>
      <a href="/privacy.html">Privacy</a>
      <a href="/terms.html">Terms</a>
      <a href="/data-deletion.html">Account deletion</a>
    </nav>
  </footer>;
}
