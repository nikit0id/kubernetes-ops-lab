#!/usr/bin/env bash
# Called from the post-receive hook of a disposable local source repository.
set -euo pipefail
while read -r _ sha ref; do
    [[ "$ref" == refs/heads/rehearsal ]] || continue
    [[ "$sha" =~ ^[0-9a-f]{40}$ ]]
    git archive "$sha" | tar -x -C "$BUILD_DIR"
    # Hooks inherit GIT_DIR and other variables pointing at the source mirror.
    while read -r variable; do unset "$variable"; done < <(git rev-parse --local-env-vars)
    image="127.0.0.1:$REGISTRY_PORT/portfolio-api:$sha"
    docker build --label "org.opencontainers.image.revision=$sha" -t "$image" "$BUILD_DIR"
    docker push "$image"
    digest=$(curl -fsSI -H 'Accept: application/vnd.oci.image.index.v1+json, application/vnd.oci.image.manifest.v1+json, application/vnd.docker.distribution.manifest.list.v2+json, application/vnd.docker.distribution.manifest.v2+json' \
        "http://127.0.0.1:$REGISTRY_PORT/v2/portfolio-api/manifests/$sha" |
        awk 'tolower($1)=="docker-content-digest:" {gsub(/\r/,"",$2); print $2}')
    [[ "$digest" =~ ^sha256:[a-f0-9]{64}$ ]]
    printf 'image:\n  repository: lab-registry:5000/portfolio-api\n  tag: "%s"\n  digest: "%s"\n' \
        "$sha" "$digest" >"$RELEASE_DIR/portfolio-values.yaml"
    git -C "$RELEASE_DIR" add portfolio-values.yaml
    git -C "$RELEASE_DIR" commit -qm "Promote source $sha at $digest"
    git -C "$RELEASE_DIR" push -q origin HEAD:rehearsal
done
