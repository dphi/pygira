# Architecture boundaries

`pygira` serves both library callers and the command-line interface. The public
device facades are the boundary between application workflows and protocol
implementations:

```text
Click command
    -> target resolution
    -> G1 or X1 facade
    -> HTTP, configuration-service, or GDS transport
    -> device
```

## Dependency rules

- Command modules own option parsing, prompting, rendering, and filesystem
  input/output.
- Shared provisioning rules belong in `pygira.operations` and should be pure
  where possible.
- Commands resolve normal device targets through
  `pygira.commands._target.resolve_device`.
- Commands do not construct `ApiClient` or other transport clients directly.
- `G1` and `X1` select device-specific protocol behavior and expose consistent
  operations where the devices share a capability.
- Transport modules own wire formats, authentication, retries, timeouts, and
  protocol error translation.
- Stable device responses are normalized into models. Raw responses remain
  available through explicitly low-level APIs for protocol research.

TKS-IP is a separate physical device and does not currently have a unified
facade. Its bootstrap daemon and on-demand web application remain separate
transports until their supported capability surface is stable.

## Adding behavior

1. Add or extend a normalized model when the firmware response is stable.
2. Implement the wire operation in the relevant transport.
3. Expose it through the appropriate device facade.
4. Put cross-command transformation or orchestration in `pygira.operations`.
5. Keep the command itself limited to input, output, and confirmation.
6. Add a sanitized firmware contract when behavior differs by firmware family.

Expected operational failures use the public `PygiraError` hierarchy. Catch
only errors a workflow can deliberately recover from; unexpected exceptions
must reach the top-level boundary so programming defects remain visible.
