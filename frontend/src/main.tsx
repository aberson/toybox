import { StrictMode } from "react";
import type { JSX } from "react";
import { createRoot } from "react-dom/client";

import { Parent } from "./parent/Parent";
import { Child } from "./child/Child";

// Phase A: simple path-based routing. Step 9/10 may swap in react-router
// once nested routing is needed; for the skeleton, a plain switch on
// `window.location.pathname` is enough to render two distinct pages.
function App(): JSX.Element {
  const path = window.location.pathname;
  if (path.startsWith("/child")) {
    return <Child />;
  }
  if (path.startsWith("/parent")) {
    return <Parent />;
  }
  return (
    <main>
      <h1>Toybox</h1>
      <p>
        Visit <a href="/parent">/parent</a> or <a href="/child">/child</a>.
      </p>
    </main>
  );
}

const container = document.getElementById("root");
if (container === null) {
  throw new Error("missing #root element in index.html");
}
createRoot(container).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
