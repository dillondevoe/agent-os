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
        ];
      };

      # Prove boot-and-talk in a VM BEFORE it ever touches the Dell:
      #   nix build .#vm && ./result/bin/run-*-vm
      packages.${system}.vm =
        self.nixosConfigurations.agentos.config.system.build.vm;

      # Phase 2 · Step 1 — evaluating the capability registry FORCES its invariant
      # assertions (mechanism 3 + INV-2 + schema). Any violation throws during eval,
      # so `nix flake check` fails to build this. That failure IS the test — a
      # configuration that breaks a security invariant does not evaluate.
      checks.${system}.capability-registry =
        let reg = import ./modules/capability-registry.nix { lib = nixpkgs.lib; };
        in nixpkgs.legacyPackages.${system}.runCommand "capability-registry-check" { } ''
          echo "capability registry invariants hold (ok=${builtins.toJSON reg.ok})"
          echo "capabilities: ${nixpkgs.lib.concatStringsSep " " reg.capabilityNames}"
          touch $out
        '';
    };
}
