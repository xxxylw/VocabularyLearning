/** ICP and public-security filing links for the Fusio home screen. */
export function IcpFooter() {
  return (
    <footer className="icp-footer">
      <a href="https://beian.miit.gov.cn" target="_blank" rel="noopener">
        陇ICP备2026010404号
      </a>
      <a
        href="http://www.beian.gov.cn/portal/registerSystemInfo?recordcode=62050302000397"
        target="_blank"
        rel="noopener"
      >
        <img className="icp-footer-badge" src="/gongan.png" alt="" width="12" height="12" />
        甘公网安备62050302000397号
      </a>
    </footer>
  );
}
