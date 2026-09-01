import type { UserResponse } from "../api/generated/types.gen";
import {
  vApiLoginApiV1LoginPostResponse,
  vApiLogoutApiV1LogoutPostResponse,
  vApiSessionApiV1SessionGetResponse,
  vChangeOwnPasswordApiV1PasswordPostResponse,
} from "../api/generated/valibot.gen";
import { ApiError, ApiTransport } from "../api/transport";

export type AuthState =
  | { phase: "loading" }
  | { phase: "anonymous"; notice?: string }
  | { phase: "unavailable" }
  | {
      phase: "authenticated" | "restricted";
      user: EffectiveUser;
      pending: boolean;
      error?: string;
    };

type Listener = (state: AuthState) => void;

export type EffectiveUser = UserResponse & { effective_idle_minutes: number };

const SIGN_IN_AGAIN = "Your session ended. Please sign in again.";
const UNAVAILABLE = "Markweave is unavailable. Try again shortly.";

export class AuthController {
  private state: AuthState = { phase: "loading" };
  private readonly listeners = new Set<Listener>();
  private generation = 0;
  private request?: AbortController;
  private operationPending = false;

  constructor(private readonly api: ApiTransport = new ApiTransport()) {}

  snapshot = (): AuthState => this.state;

  subscribe = (listener: Listener): (() => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  dispose(): void {
    this.generation += 1;
    this.request?.abort();
    this.operationPending = false;
  }

  async load(): Promise<void> {
    const { controller, generation } = this.start();
    this.publish({ phase: "loading" });
    try {
      const user = await this.api.json(
        "/api/v1/session",
        vApiSessionApiV1SessionGetResponse,
        { signal: controller.signal },
      );
      if (this.current(generation)) this.accept(user);
    } catch (error) {
      if (!this.current(generation) || isAbort(error)) return;
      this.publish(
        error instanceof ApiError && error.status === 401
          ? { phase: "anonymous" }
          : { phase: "unavailable" },
      );
    }
  }

  async login(username: string, password: string): Promise<void> {
    if (this.pending()) return;
    this.operationPending = true;
    const { controller, generation } = this.start();
    this.publishPending(true);
    try {
      const result = await this.api.json(
        "/api/v1/login",
        vApiLoginApiV1LoginPostResponse,
        {
          body: JSON.stringify({ username, password }),
          method: "POST",
          signal: controller.signal,
        },
      );
      if (this.current(generation)) {
        this.operationPending = false;
        this.accept(result.user);
      }
    } catch (error) {
      if (!this.current(generation) || isAbort(error)) return;
      this.operationPending = false;
      const message =
        error instanceof ApiError && error.code === "INVALID_CREDENTIALS"
          ? "Username or password is incorrect."
          : "Sign-in could not be completed. Try again.";
      this.publish({ phase: "anonymous", notice: message });
    }
  }

  async logout(): Promise<void> {
    if (!hasUser(this.state) || this.pending()) return;
    this.operationPending = true;
    const previous = this.state;
    const { controller, generation } = this.start();
    this.publishPending(true);
    try {
      await this.api.json("/api/v1/logout", vApiLogoutApiV1LogoutPostResponse, {
        csrf: true,
        method: "POST",
        signal: controller.signal,
      });
      if (this.current(generation)) {
        this.operationPending = false;
        this.publish({ phase: "anonymous" });
      }
    } catch (error) {
      if (!this.current(generation) || isAbort(error)) return;
      this.operationPending = false;
      if (
        error instanceof ApiError &&
        (error.status === 401 || error.code === "CSRF_MISSING")
      ) {
        this.publish({ phase: "anonymous", notice: SIGN_IN_AGAIN });
      } else {
        this.publish({
          ...previous,
          pending: false,
          error: "Sign-out failed. Try again.",
        });
      }
    }
  }

  async renew(password: string, confirmation: string): Promise<void> {
    if (this.state.phase !== "restricted" || this.state.pending) return;
    this.operationPending = true;
    const previous = this.state;
    const { controller, generation } = this.start();
    this.publishPending(true);
    try {
      await this.api.json(
        "/api/v1/password",
        vChangeOwnPasswordApiV1PasswordPostResponse,
        {
          body: JSON.stringify({ password, confirmation }),
          csrf: true,
          method: "POST",
          signal: controller.signal,
        },
      );
      if (this.current(generation)) {
        this.operationPending = false;
        this.publish({
          phase: "anonymous",
          notice: "Password changed. Sign in with your new password.",
        });
      }
    } catch (error) {
      if (!this.current(generation) || isAbort(error)) return;
      this.operationPending = false;
      if (
        error instanceof ApiError &&
        (error.status === 401 || error.code === "CSRF_MISSING")
      ) {
        this.publish({ phase: "anonymous", notice: SIGN_IN_AGAIN });
        return;
      }
      const errorMessage =
        error instanceof ApiError &&
        error.code === "PASSWORD_CONFIRMATION_INVALID"
          ? "The passwords do not match."
          : error instanceof ApiError && error.code === "PASSWORD_INVALID"
            ? "Enter a valid new password."
            : "The password could not be changed. Try again.";
      this.publish({ ...previous, pending: false, error: errorMessage });
    }
  }

  expire(): void {
    if (this.state.phase === "anonymous") return;
    this.dispose();
    this.publish({ phase: "anonymous", notice: SIGN_IN_AGAIN });
  }

  unavailableMessage(): string {
    return UNAVAILABLE;
  }

  private start(): { controller: AbortController; generation: number } {
    this.request?.abort();
    this.request = new AbortController();
    this.generation += 1;
    return { controller: this.request, generation: this.generation };
  }

  private current(generation: number): boolean {
    return generation === this.generation;
  }

  private accept(user: UserResponse): void {
    if (
      user.effective_idle_minutes == null ||
      !Number.isInteger(user.effective_idle_minutes) ||
      user.effective_idle_minutes <= 0
    )
      throw new TypeError("Invalid authenticated user response");
    const effectiveUser = user as EffectiveUser;
    this.publish({
      phase: user.password_change_required ? "restricted" : "authenticated",
      user: effectiveUser,
      pending: false,
    });
  }

  private pending(): boolean {
    return this.operationPending;
  }

  private publishPending(pending: boolean): void {
    if (hasUser(this.state)) this.publish({ ...this.state, pending });
  }

  private publish(state: AuthState): void {
    this.state = state;
    for (const listener of this.listeners) listener(state);
  }
}

function isAbort(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

function hasUser(
  state: AuthState,
): state is Extract<AuthState, { user: EffectiveUser }> {
  return state.phase === "authenticated" || state.phase === "restricted";
}
