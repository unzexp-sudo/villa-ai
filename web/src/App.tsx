import Landing from "./landing/Landing";
import Studio from "./studio/Studio";
import Interior from "./interior/Interior";
import { PRODUCT_NAME } from "./contract";

// Route switch, no router dependency. Vite's history fallback serves /app and
// /interior. The interior section is ADDITIVE: it does not replace the MEP studio.
export default function App() {
  const path = window.location.pathname;
  const isStudio = path.startsWith("/app");
  const isInterior = path.startsWith("/interior");

  let page = <Landing />;
  if (isStudio) page = <Studio />;
  else if (isInterior) page = <Interior />;

  return (
    <>
      <header className="app-topbar">
        <div className="brand">
          {PRODUCT_NAME.slice(0, 5)}<span>{PRODUCT_NAME.slice(5)}</span>
        </div>
        <nav>
          <a href="/" className={!isStudio && !isInterior ? "active" : ""}>Overview</a>
          <a href="/app" className={isStudio ? "active" : ""}>Open the app</a>
          <a href="/interior" className={isInterior ? "active" : ""}>Interior 3D</a>
        </nav>
      </header>
      {page}
    </>
  );
}
