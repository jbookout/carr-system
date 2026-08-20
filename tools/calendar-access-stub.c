/* calendar-access-stub.c — the main executable of "CARR Calendar Access.app".
 *
 * WHY THIS EXISTS. The bundle's executable used to BE the zsh script now living
 * at Contents/Resources/run.zsh. That worked until macOS 26: Launch Services
 * there refuses to launch an app bundle whose main executable is a script, and
 * answers -10669 without ever running it. Measured on Dell's Mac 2026-08-18,
 * macOS 26.5.2 (25F84), with two otherwise-identical throwaway bundles:
 *
 *     main executable = zsh script  -> open failed, error -10669
 *     main executable = Mach-O      -> launched, exit 0
 *
 * So the fix is ONLY about the executable's FORMAT. This stub is a Mach-O, which
 * satisfies Launch Services, and it immediately execs the same zsh script with
 * the same arguments. Behavior is unchanged; the bundle is launchable again.
 *
 * WHY exec RATHER THAN spawn-and-wait. The whole point of the bundle is TCC
 * identity: macOS attributes the calendar grant to the RESPONSIBLE process, which
 * is the app that Launch Services started. execv replaces this image in place, so
 * the PID and its responsible-process attribution are unchanged — which is
 * exactly the arrangement that already worked on Joe's Mac, where the launched
 * image simply happened to be zsh from the start.
 *
 * Built by bin/build-calendar-access.sh. The compiled binary and the bundle's
 * signature are deliberately NOT tracked in git: both are per-machine. A signed
 * binary committed on one Mac arrives invalid on the other ("code or signature
 * have been modified"), which is the second half of what was broken here.
 */
#include <mach-o/dyld.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

int main(int argc, char **argv) {
    char exe[PATH_MAX];
    uint32_t size = sizeof(exe);
    if (_NSGetExecutablePath(exe, &size) != 0) {
        fprintf(stderr, "carr-calendar-access: cannot locate own path\n");
        return 70;
    }

    char resolved[PATH_MAX];
    if (realpath(exe, resolved) == NULL) {
        fprintf(stderr, "carr-calendar-access: cannot resolve own path\n");
        return 70;
    }

    /* .../Contents/MacOS/carr-calendar-access -> .../Contents */
    for (int up = 0; up < 2; up++) {
        char *slash = strrchr(resolved, '/');
        if (slash == NULL) {
            fprintf(stderr, "carr-calendar-access: unexpected bundle layout\n");
            return 70;
        }
        *slash = '\0';
    }

    char script[PATH_MAX];
    if (snprintf(script, sizeof(script), "%s/Resources/run.zsh", resolved) >= (int)sizeof(script)) {
        fprintf(stderr, "carr-calendar-access: bundle path too long\n");
        return 70;
    }

    if (access(script, R_OK) != 0) {
        fprintf(stderr, "carr-calendar-access: missing %s\n", script);
        return 70;
    }

    /* /bin/zsh <script> [original args...] NULL */
    char **args = calloc((size_t)argc + 3, sizeof(char *));
    if (args == NULL) return 70;
    args[0] = "/bin/zsh";
    args[1] = script;
    for (int i = 1; i < argc; i++) args[i + 1] = argv[i];
    args[argc + 1] = NULL;

    execv("/bin/zsh", args);
    fprintf(stderr, "carr-calendar-access: exec failed\n");
    return 71;
}
