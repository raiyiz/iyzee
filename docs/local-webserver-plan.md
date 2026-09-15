# Local webserver: one Textual app, two front doors

## Goal

Expose the existing iyzee UI in a local browser **without building a second web frontend**.

The key architectural choice is to keep `IyzeeApp` as the UI/application object and let Textual render that same widget tree through its terminal renderer or its browser transport. Connect, Sweep, Traces, and Console remain the same screens; there is no React/HTML duplicate to keep in sync.

## Proposed architecture

```text
                 terminal
                    |
              Textual renderer
                    |
                 IyzeeApp
                    |
        +-----------+-----------+
        |           |           |
     Connect      Sweep      Traces/Console
        |           |           |
        +-----------+-----------+
                    |
          existing experiment layer
                    |
          instrument handles + locks
                    |
              physical hardware

                 browser
                    |
          Textual web transport
                    |
                 IyzeeApp
```

The important boundary is therefore **presentation transport, not application logic**. We should not create `WebApp` equivalents of the current screens.

## First experiment: zero application changes

Textual supports serving an existing Textual application to a browser. First prove that the current app works through that path before changing iyzee architecture:

```sh
uv sync
uv run textual serve "uv run iyzee-tui"
```

If this works, we have already demonstrated the central requirement: the same `IyzeeApp` instance and the same `Screen` classes can be displayed somewhere other than a terminal.

## What should remain shared

Everything below should remain one implementation:

- `IyzeeApp` and its mode/screen selection
- `ConnectScreen`
- `SweepScreen`
- `TracesScreen`
- `ConsoleScreen`
- Textual widgets and CSS
- instrument handles and per-instrument locks
- `last_run` / result state
- experiment/procedure/IO code
- instrument drivers
- the embedded IPython console semantics

In particular, do **not** extract the screens into a terminal implementation and a browser implementation. Textual should be the abstraction that owns rendering/input transport.

## What may need an abstraction later

Only introduce a small capability/configuration boundary when an actual browser incompatibility appears. Examples might be clipboard behavior, browser-specific key handling, window sizing, or lifecycle/session information.

Prefer:

```python
class UiEnvironment:
    # small, explicit capabilities
    ...
```

or a similarly narrow mechanism over scattering `if web:` through every screen.

The screens should continue to describe **what the user can do**, not **how the pixels reach the user**.

## Important hardware/session constraint

The current application owns real instruments and deliberately serializes access with per-instrument locks. A browser deployment must not accidentally turn one physical lab into multiple independent hardware-owning sessions.

For the first version:

1. bind to localhost by default;
2. keep one `IyzeeApp`/hardware owner per local server process;
3. treat multiple browser tabs as clients of that same app, or explicitly reject a second client if Textual's lifecycle requires it;
4. do not expose the server on the LAN by default;
5. make remote access/authentication a separate future feature.

The embedded console makes this especially important: it provides arbitrary Python access to the live lab and therefore is effectively a hardware-control surface, not just a display.

## Validation plan

Before adding any new web-specific code, test the existing application through the browser:

- application startup and screen switching;
- connect/disconnect;
- a short sweep;
- live sweep progress and trace rendering;
- traces/result browsing;
- Console input, output, history, and completion;
- `lab`/result state visibility from the console;
- matplotlib figure rendering;
- Escape/j/k navigation and function-key navigation;
- browser resize and scrolling;
- clipboard/paste where relevant;
- disconnect/reconnect while a worker is active;
- two browser tabs against the same local server.

## Then add a first-class iyzee entry point

Once the raw Textual serving path is proven, add a thin command such as:

```sh
iyzee serve --host 127.0.0.1 --port 8000
```

That command should be a wrapper around the same `IyzeeApp`, not a new application. Keep the server-specific code at the entry-point boundary.

Suggested implementation sequence:

1. Verify `textual serve` with the current `refab` application.
2. Fix only concrete browser/transport compatibility issues.
3. Add `iyzee serve` as a thin, documented wrapper.
4. Define explicit single-process hardware ownership semantics.
5. Add automated smoke/UI tests for the web-serving path where practical.
6. Document the local-only safety model.
7. Only later consider an HTTP/API layer for machine automation.

## Things we should explicitly avoid

- A second React/Vue/Svelte frontend for the same screens.
- A REST API as the first step.
- Moving the IPython console into a separate browser backend just because it is web-accessible.
- Making instrument drivers aware of Textual or HTTP.
- Duplicating experiment logic for browser use.
- Binding the control server to `0.0.0.0` by default.

## Longer-term separation

There are two different future requirements and they should stay separate:

**Human UI:** browser/terminal display of the existing Textual application. Textual owns the UI transport.

**Machine automation:** a deliberate API for starting experiments, reading results, and managing instruments. That API can eventually sit beside `IyzeeApp` and call the same experiment/domain layer, but it should not be confused with the browser rendering mechanism.

## Definition of done for this phase

A user can run one local command, open a browser, and use the existing iyzee Connect/Sweep/Traces/Console UI with the same behavior and application state as the terminal version, with no duplicate frontend implementation and no additional hardware-control path.
