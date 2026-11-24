// store: one state object, changed only by dispatching actions through a pure reducer (see actions.js).
// Subscribers get (state, prev, action, meta) after every change. `meta` is free-form context for the
// listeners (for example { from: "wave" } or { remote: true }); the reducer never sees it.

export function createStore(reduce, initial) {
  let state = initial;
  const subs = new Set();
  return {
    get: () => state,
    /** Apply an action. Returns the new state. Subscribers run only when the state object changed. */
    dispatch(action, meta = {}) {
      const prev = state;
      state = reduce(state, action);
      if (state !== prev) for (const fn of [...subs]) fn(state, prev, action, meta);
      return state;
    },
    /** Returns an unsubscribe function. */
    subscribe(fn) {
      subs.add(fn);
      return () => subs.delete(fn);
    },
  };
}
