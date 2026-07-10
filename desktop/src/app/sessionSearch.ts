export type SessionSearchDebouncer = {
  schedule: (task: () => void) => void;
  cancel: () => void;
};


export function createSessionSearchDebouncer(delayMs = 250): SessionSearchDebouncer {
  let timer: ReturnType<typeof setTimeout> | null = null;

  function cancel() {
    if (timer !== null) {
      clearTimeout(timer);
      timer = null;
    }
  }

  return {
    schedule(task) {
      cancel();
      timer = setTimeout(() => {
        timer = null;
        task();
      }, Math.max(0, delayMs));
    },
    cancel,
  };
}
