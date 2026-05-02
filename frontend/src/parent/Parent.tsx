import type { JSX } from "react";

import { ErrorCode } from "../shared/errors";

// Phase A placeholder. Step 9 fills in suggestion card, activity panel,
// mic-hot indicator, etc. Importing ErrorCode here proves the codegen
// pipeline is wired end-to-end.
export function Parent(): JSX.Element {
  const codeCount = Object.keys(ErrorCode).length;
  return (
    <main>
      <h1>Toybox Parent</h1>
      <p>route: /parent</p>
      <p>error codes loaded: {codeCount}</p>
    </main>
  );
}
