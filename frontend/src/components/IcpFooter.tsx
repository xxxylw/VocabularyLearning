/**
 * Regulatory filing footer, required by Chinese regulation for fusio.com.cn:
 * the MIIT ICP filing and the public-security (公安) networked filing.
 * Rendered once at the app root so it shows on every public view
 * (login/register and the signed-in app). Intentionally the only
 * Chinese-language element in the English UI.
 *
 * index.html carries a static copy of this markup inside #root as a
 * pre-React placeholder (keeps the record numbers visible to curl /
 * crawlers); React replaces it with this component on mount. Keep the
 * two in sync when editing.
 */
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
