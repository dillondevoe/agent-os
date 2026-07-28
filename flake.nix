{
  description = "Agent OS — a computer whose shell is an agent, not a desktop";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs = { self, nixpkgs }:
    let
      system = "x86_64-linux";  # Dell Latitude = Intel x86_64
    in {
      # The whole machine. `agent-shell.nix` is the part that makes it Agent OS;
      # `configuration.nix` is boring base plumbing (bootloader, user, network).
      nixosConfigurations.agentos = nixpkgs.lib.nixosSystem {
        inherit system;
        modules = [
          ./configuration.nix
          ./modules/agent-shell.nix
          ./modules/brain.nix
          ./modules/audit.nix
          ./modules/taint.nix
          ./modules/mcp.nix
          ./modules/broker.nix
        ];
      };

      # Prove boot-and-talk in a VM BEFORE it ever touches the Dell:
      #   nix build .#vm && ./result/bin/run-*-vm
      packages.${system}.vm =
        self.nixosConfigurations.agentos.config.system.build.vm;

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
      };
    };
}
