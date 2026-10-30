/**
 * ICP filing footer, required by Chinese regulation for fusio.com.cn.
 * Rendered once at the app root so it shows on every public view
 * (login/register and the signed-in app). Intentionally the only
 * Chinese-language element in the English UI.
 */
export function IcpFooter() {
  return (
    <footer className="icp-footer">
      <a href="https://beian.miit.gov.cn" target="_blank" rel="noopener">
        陇ICP备2026010404号
      </a>
    </footer>
  );
}
