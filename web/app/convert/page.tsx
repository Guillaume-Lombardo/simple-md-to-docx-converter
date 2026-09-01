import {
  Alert,
  AppShell,
  LoadingStatus,
  Progress,
  TextField,
} from "../../components/primitives";

export default function FoundationPage() {
  return (
    <AppShell current="Convert">
      <h1 className="text-3xl font-semibold">Convert</h1>
      <Alert>
        This preview foundation does not replace the production conversion page
        yet.
      </Alert>
      <form className="max-w-xl space-y-5" aria-label="Foundation form">
        <TextField label="Document label" name="document-label" />
        <Progress label="Foundation progress" value={0} />
        <LoadingStatus loading={false}>
          Ready for workflow migration.
        </LoadingStatus>
      </form>
    </AppShell>
  );
}
