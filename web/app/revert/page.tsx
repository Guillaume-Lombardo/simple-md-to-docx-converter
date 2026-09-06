import { Protected } from "../../src/auth/context";
import { ReversionWorkspace } from "../../src/reversion/workspace";

export default function RevertPage() {
  return (
    <Protected>
      <ReversionWorkspace />
    </Protected>
  );
}
