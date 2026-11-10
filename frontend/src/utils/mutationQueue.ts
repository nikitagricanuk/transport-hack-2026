/** Serialize whole-state mutations so response order cannot hide another edit. */
export function createMutationQueue<T>() {
  let tail: Promise<void> = Promise.resolve();
  return {
    run(request: () => Promise<T>): Promise<T> {
      const result = tail.then(request);
      tail = result.then(
        () => undefined,
        () => undefined,
      );
      return result;
    },
  };
}
