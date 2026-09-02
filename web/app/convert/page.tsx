import { Protected } from "../../src/auth/context";
import { ConversionWorkspace } from "../../src/conversion/workspace";

export default function ConvertPage() {
  return (
    <Protected>
      <ConversionWorkspace />
    </Protected>
  );
}
