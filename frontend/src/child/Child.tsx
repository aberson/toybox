import type { JSX } from "react";

import { ErrorCode } from "../shared/errors";

// Phase A placeholder. Step 10 fills in the kiosk activity view.
// Importing ErrorCode here proves the codegen pipeline is wired
// end-to-end on the child side as well.
export function Child(): JSX.Element {
  const codeCount = Object.keys(ErrorCode).length;
  return (
    <main>
      <h1>Toybox Child</h1>
      <p>route: /child</p>
      <p>error codes loaded: {codeCount}</p>
    </main>
  );
}
