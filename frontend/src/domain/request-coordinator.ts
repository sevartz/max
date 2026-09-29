type RequestHandlers<T> = {
  onSuccess: (value: T) => void;
  onError: (error: unknown) => void;
  onSettled?: () => void;
};

/** Coalesces same-key reads and prevents invalidated/older responses from committing. */
export class RequestCoordinator {
  private generations = new Map<string, number>();
  private flights = new Map<string, Promise<unknown>>();
  private controllers = new Map<string, AbortController>();

  run<T>(key: string, load: (signal: AbortSignal) => Promise<T>, handlers: RequestHandlers<T>): Promise<T> {
    const existing = this.flights.get(key);
    if (existing) return existing as Promise<T>;
    const generation = (this.generations.get(key) ?? 0) + 1;
    this.generations.set(key, generation);
    const controller = new AbortController();
    this.controllers.set(key, controller);
    const current = () => this.generations.get(key) === generation;
    const request = Promise.resolve().then(() => load(controller.signal)).then((value) => {
      if (current()) handlers.onSuccess(value);
      return value;
    }).catch((error: unknown) => {
      if (current()) handlers.onError(error);
      throw error;
    }).finally(() => {
      if (this.flights.get(key) === request) this.flights.delete(key);
      if (this.controllers.get(key) === controller) this.controllers.delete(key);
      if (current()) handlers.onSettled?.();
    });
    this.flights.set(key, request);
    return request;
  }

  invalidate(key: string): void {
    this.generations.set(key, (this.generations.get(key) ?? 0) + 1);
    this.flights.delete(key);
    this.controllers.get(key)?.abort();
    this.controllers.delete(key);
  }
}
