{
  description = "Agent OS — a computer whose shell is an agent, not a desktop";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs = { self, nixpkgs }:
    let
      system = "x86_64-linux";  # Dell Latitude = Intel x86_64
      # One module list, two machines: `agentos` ships UNSEALED (provisioning — the model
      # can still be pulled); `agentos-sealed` is the SAME machine with the clean-room
      # egress wall sealed to nixpkgs-only. The on-device seal is therefore a single
      # `nixos-rebuild switch --flake ...#agentos-sealed` AFTER the model pull — no local
      # file editing, no git push.
      baseModules = [
        ./configuration.nix
        ./modules/agent-shell.nix
        ./modules/brain.nix
        ./modules/clean-room.nix
        ./modules/audit.nix
        ./modules/taint.nix
        ./modules/mcp.nix
        ./modules/broker.nix
        ./modules/confirm.nix
        ./modules/seal-check.nix
        ./modules/break-glass.nix   # PR-A: the ONE interactive root door (tty3, password-gated)
        ./modules/system-set.nix    # PR-A: SCAFFOLD for the root-side system.set executor (impl in PR-J)
        ./modules/boot-branding.nix
      ];
      mkSystem = extraModules: nixpkgs.lib.nixosSystem {
        inherit system;
        modules = baseModules ++ extraModules;
      };
    in {
      # The whole machine. `agent-shell.nix` is the part that makes it Agent OS;
      # `configuration.nix` is boring base plumbing (bootloader, user, network).
      nixosConfigurations.agentos = mkSystem [ ];
      # Same machine, egress wall sealed — the post-model-pull `switch` target.
      nixosConfigurations.agentos-sealed = mkSystem [ { agentos.cleanRoom.sealed = true; } ];

      # Prove boot-and-talk in a VM BEFORE it ever touches the Dell:
      #   nix build .#vm && ./result/bin/run-*-vm       (unsealed: can pull a model)
      #   nix build .#vm-sealed                          (sealed: nixpkgs-only egress)
      packages.${system} = {
        vm        = self.nixosConfigurations.agentos.config.system.build.vm;
        vm-sealed = self.nixosConfigurations.agentos-sealed.config.system.build.vm;

        # seal-failloud fail-down loop, driven headless (nixosTest). Kept OUT of `checks`
        # so routine `nix flake check` stays fast — this boots a VM (minutes) vs the 6
        # property batteries (seconds). Run on demand:  nix build .#test-seal-faildown
        test-seal-faildown = import ./tests/seal-faildown.nix {
          pkgs = nixpkgs.legacyPackages.${system};
          inherit baseModules;
        };
      };

      checks.${system} = {
        # Phase 2 · Step 1 — evaluating the capability registry FORCES its invariant
        # assertions (mechanism 3 + INV-2 + schema). Any violation throws during eval,
        # so `nix flake check` fails to build this. That failure IS the test — a
        # configuration that breaks a security invariant does not evaluate.
        capability-registry =
          let reg = import ./modules/capability-registry.nix { lib = nixpkgs.lib; };
          in nixpkgs.legacyPackages.${system}.runCommand "capability-registry-check" { } ''
            echo "capability registry invariants hold (ok=${builtins.toJSON reg.ok})"
            echo "capabilities: ${nixpkgs.lib.concatStringsSep " " reg.capabilityNames}"
            touch $out
          '';

        # Phase 2 · Step 2 — the audit-log primitive's property battery. Proves
        # append-only NDJSON, SHA-256 chain tamper/truncation evidence, no-log->no-execute
        # fail-closed, hostile-newline single-line safety, and reserved-field forgery
        # stripping. A regression here fails `nix flake check`.
        audit-log =
          nixpkgs.legacyPackages.${system}.runCommand "audit-log-check"
            { nativeBuildInputs = [ nixpkgs.legacyPackages.${system}.python3 ]; } ''
              work="$(mktemp -d)"
              bash ${./tests/audit-battery.sh} ${./bin/audit} "$work"
              touch $out
            '';

        # Phase 2 · Step 3 — the taint tracker's property battery (SHADOW mode). Proves
        # the anti-laundering invariants: a monotonic set-only per-session bit, human-only
        # reset, UNTRUSTED-absorbing mem origin tags stored where the model can't write,
        # recall-of-untrusted re-taints across sessions, boot-taint on untrusted mem, and
        # fail-closed audit logging that gates NOTHING in v1. It drives the real audit
        # primitive, so a regression in either fails `nix flake check`.
        taint-shadow =
          nixpkgs.legacyPackages.${system}.runCommand "taint-shadow-check"
            { nativeBuildInputs = [ nixpkgs.legacyPackages.${system}.python3 ]; } ''
              work="$(mktemp -d)"
              bash ${./tests/taint-battery.sh} ${./bin/taint} ${./bin/audit} "$work"
              touch $out
            '';

        # Phase 2 · Step 4 — the MCP stdio front door's conformance + hostile-input
        # battery. Proves the parser invariants: one pinned protocol rev, three methods
        # only, and fail-closed on every unknown/malformed/oversized/duplicate-id/
        # duplicate-key/type-coerced/trailing-byte/unicode-trick input, with hard
        # size+depth caps and NO parser differential (one parser, one schema, no
        # coercion). The parser is pure (no privileged state), so this check needs no
        # scratch dirs beyond a workdir. A regression fails `nix flake check`.
        mcp-conformance =
          nixpkgs.legacyPackages.${system}.runCommand "mcp-conformance-check"
            { nativeBuildInputs = [ nixpkgs.legacyPackages.${system}.python3 ]; } ''
              work="$(mktemp -d)"
              bash ${./tests/mcp-battery.sh} ${./bin/mcp} "$work"
              touch $out
            '';

        # Phase 2 · Step 5 — the broker's property battery ("the wall"). Proves the 10-stage
        # fail-closed pipeline against the REAL materialized registry + the REAL taint/audit
        # primitives as children: the routing matrix (T0 ALLOW-AUTO, T1/T2 REQUIRE-CONFIRM),
        # T3 non-expressibility, per-type arg validation (path canonical+confined via shared
        # golden vectors, url INV-2 + obfuscated-IP evasion, namespace-under-root, recipient
        # charset, enum-denies-pre-GAP-1), verdict passthrough, single-flight + malformed-line
        # stream-shut, no-log->no-execute, the return-path taint-before-content ordering
        # (origin broker-derived not impl-reported, withhold-on-taint-fail, broker-owns-the-
        # stamp), the DATA fence, confirm epoch binding, and mcp-input-contract compatibility.
        # It materializes the registry with the SAME builtins.toJSON the broker module ships,
        # so a registry change that would break classification fails `nix flake check` here.
        broker-core =
          let
            reg = import ./modules/capability-registry.nix { lib = nixpkgs.lib; };
            pkgs = nixpkgs.legacyPackages.${system};
            registryJson = pkgs.writeText "registry.json" (builtins.toJSON reg.registry);
          in pkgs.runCommand "broker-core-check" { nativeBuildInputs = [ pkgs.python3 ]; } ''
            work="$(mktemp -d)"
            bash ${./tests/broker-battery.sh} ${./bin/broker} ${./bin/taint} ${./bin/audit} \
              ${./bin/mcp} ${registryJson} "$work"
            touch $out
          '';

        # Phase 2 · Step 6 — the confirm channel ("the wall's mouth") property battery. Proves the
        # broker-owned confirm client (bin/confirm) is a fail-closed renderer/relayer/collector:
        # seam contract on both backends, no-channel/unreachable/unknown-name all deny (never
        # allow), nonce echo + domain-separated-HMAC auth (reflection/badmac/wrong-user/epoch each
        # caught), human-window + EOF timeouts, field-confusion/injection scrubbing with the `│ `
        # sentinel on every model-controlled value line while the trust region stays intact,
        # parse_mode-None transmitted frames, first-time-destination highlighting, preview
        # truncation, single-flight non-multiplexing, and a REAL-broker integration leg
        # (route->confirm->approve->invoke / route->confirm->deny->withhold) whose audit chain
        # still verifies and carries the nonce. It drives the real broker/taint/audit as children,
        # so a regression in any of them fails `nix flake check` here.
        confirm-channel =
          let
            reg = import ./modules/capability-registry.nix { lib = nixpkgs.lib; };
            pkgs = nixpkgs.legacyPackages.${system};
            registryJson = pkgs.writeText "registry.json" (builtins.toJSON reg.registry);
          in pkgs.runCommand "confirm-channel-check" { nativeBuildInputs = [ pkgs.python3 ]; } ''
            work="$(mktemp -d)"
            bash ${./tests/confirm-battery.sh} ${./bin/confirm} ${./bin/broker} ${./bin/taint} \
              ${./bin/audit} ${registryJson} "$work"
            touch $out
          '';

        # Phase 4 (v0.2 A1) — the tool-calling loop's property battery. Drives the REAL
        # bin/agent-loop against a scripted fake Ollama (tests/ollama-stub.py) over the
        # sandbox loopback and proves the loop MECHANICS: a plain answer skips tools; a valid
        # echo call round-trips (dispatch -> result fed back as a role:"tool" message that
        # preserves the content_type:"data" envelope -> final answer); three denials in one
        # user turn stop tool-calling and the final turn is taken with NO tools offered;
        # tool_calls emitted on that withheld final turn are NEVER dispatched; and model-
        # supplied terminal control bytes are scrubbed before the tty. agent-loop lives on the
        # UNTRUSTED side and makes zero security decisions, so this is loop correctness, not a
        # wall test. A regression fails `nix flake check`.
        agent-loop =
          nixpkgs.legacyPackages.${system}.runCommand "agent-loop-check"
            { nativeBuildInputs = [ nixpkgs.legacyPackages.${system}.python3 ]; } ''
              work="$(mktemp -d)"
              bash ${./tests/agent-loop-battery.sh} ${./bin/agent-loop} ${./tests/ollama-stub.py} "$work"
              touch $out
            '';
      };
    };
}
