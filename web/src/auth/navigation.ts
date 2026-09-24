export function redirectProtected(
  destination: string,
  replaceRoute: (path: string) => void,
  location: Pick<Location, "pathname" | "replace"> = window.location,
): void {
  if (location.pathname === "/composer") location.replace(destination);
  else replaceRoute(destination);
}
