import { useRef, useState, type ChangeEvent, type FormEvent } from "react";
import { Camera, CheckCircle, Trash, WarningCircle } from "@phosphor-icons/react";
import { api } from "@/api/client";
import { useAuth } from "@/auth/AuthContext";
import { useToast } from "@/auth/ToastContext";
import { Button, Card, Field } from "@/components/ui";
import {
  AVATAR_ACCEPT,
  avatarSrc,
  displayNameFor,
  initialsFor,
  validateAvatarFile,
} from "@/lib/avatar";
import type { UpdateProfileRequest } from "@/api/types";

/** Matches the API's own column limit, so the field cannot over-type the write. */
const NAME_MAX_LENGTH = 50;

/** Ties both name inputs to the one message the PATCH error produces. */
const FORM_ERROR_ID = "profile-form-error";

/**
 * Name, picture, and the account identifiers that cannot be edited.
 *
 * The avatar markup is written here rather than reusing `components/Avatar.tsx`
 * because the shell's avatar and this preview have different jobs: the shell one
 * is a passive badge, this one is a 72px preview that must survive a `data:` URL
 * the browser can't decode (hence the `onError` fallback to initials).
 */
export function ProfileSection() {
  const { user, setUser } = useAuth();
  const { success, error } = useToast();
  const fileInput = useRef<HTMLInputElement>(null);

  // Seeded once from the cached user. Re-seeding on every `user` change would
  // fight the user: `setUser` fires after each save and after the avatar
  // upload, and a half-typed surname must not be replaced by the server's copy
  // mid-edit.
  const [firstName, setFirstName] = useState(user?.first_name ?? "");
  const [lastName, setLastName] = useState(user?.last_name ?? "");
  const [saving, setSaving] = useState(false);
  const [resending, setResending] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [removing, setRemoving] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [imageFailed, setImageFailed] = useState(false);

  const picture = avatarSrc(user);
  const showPicture = picture !== null && !imageFailed;

  // Compared trimmed, because the server normalises whitespace: a field holding
  // " Ada " for a stored "Ada" is not a change, and sending it would light up
  // Save for a no-op write.
  const currentFirst = (user?.first_name ?? "").trim();
  const currentLast = (user?.last_name ?? "").trim();
  const nextFirst = firstName.trim();
  const nextLast = lastName.trim();
  const changed = nextFirst !== currentFirst || nextLast !== currentLast;

  async function onPickFile(e: ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    // Clear the input first so picking the *same* file again still fires a
    // change event — otherwise a failed upload could not be retried without
    // choosing a different file.
    e.target.value = "";
    if (!file) return;

    // Refused locally so a 3 MB photo fails instantly instead of after the
    // upload. The server repeats the check; this is not a security control.
    const problem = validateAvatarFile(file);
    if (problem) {
      error(problem);
      return;
    }

    setUploading(true);
    try {
      // Publish the server's record rather than re-fetching: the shell's avatar
      // then changes on the same tick as this preview.
      setUser(await api.uploadAvatar(file));
      setImageFailed(false);
      success("Photo updated");
    } catch (err) {
      error(err instanceof Error ? err.message : "Could not upload that image. Try again.");
    } finally {
      setUploading(false);
    }
  }

  async function removePicture() {
    setRemoving(true);
    try {
      setUser(await api.deleteAvatar());
      success("Photo removed");
    } catch (err) {
      error(err instanceof Error ? err.message : "Could not remove your photo. Try again.");
    } finally {
      setRemoving(false);
    }
  }

  async function resendVerification() {
    // Deliberately uses the SAVED address, not an editable field: the resend
    // endpoint is keyed on the address, the address cannot be changed here, and
    // sending a link to anything else would be a link the account does not own.
    if (!user?.email) return;
    setResending(true);
    try {
      const result = await api.resendVerification(user.email);
      success(result.message || "Verification email sent");
    } catch (err) {
      error(err instanceof Error ? err.message : "Could not send the verification email");
    } finally {
      setResending(false);
    }
  }

  async function saveProfile(e: FormEvent) {
    e.preventDefault();
    if (!changed || saving) return;

    // Send only what changed. An emptied field sends `""` on purpose: the server
    // normalises that to `null`, which is the same state as never having set it.
    const body: UpdateProfileRequest = {};
    if (nextFirst !== currentFirst) body.first_name = nextFirst;
    if (nextLast !== currentLast) body.last_name = nextLast;

    setSaving(true);
    setFormError(null);
    try {
      const updated = await api.updateProfile(body);
      setUser(updated);
      // Re-seed from what the server actually stored, so the comparison above
      // resets and a cleared field cannot look "still dirty" forever.
      setFirstName(updated.first_name ?? "");
      setLastName(updated.last_name ?? "");
      success("Profile updated");
    } catch (err) {
      const message = err instanceof Error ? err.message : "Could not save your profile.";
      // Inline *and* toast: the toast is the live announcement, the inline text
      // is what a keyboard user finds still attached to the fields afterwards
      // (`aria-describedby`), so the message is not tied to a disappearing
      // notification.
      setFormError(message);
      error(message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <Card className="p-6">
      <h2 className="font-display text-lg font-semibold">Profile</h2>
      <p className="mt-1 text-sm text-muted">
        Your name and picture are how you appear in the app. Your sign-in details are below.
      </p>

      <div className="mt-5 flex flex-col gap-4 sm:flex-row sm:items-center">
        {showPicture ? (
          <img
            src={picture}
            alt=""
            width={72}
            height={72}
            // The initials tile is the fallback for a `data:` URL the browser
            // cannot decode (truncated payload, codec it refuses). Without this
            // the browser draws its own broken-image glyph in the middle of the
            // page, which reads as a broken account rather than a missing photo.
            onError={() => setImageFailed(true)}
            className="h-[72px] w-[72px] shrink-0 rounded-full border border-outline object-cover"
          />
        ) : (
          // Decorative: the display name is printed beside it.
          <span
            aria-hidden="true"
            className="flex h-[72px] w-[72px] shrink-0 items-center justify-center rounded-full border border-outline bg-surface-variant font-display text-xl font-semibold text-on-background"
          >
            {initialsFor(user)}
          </span>
        )}

        <div className="min-w-0 space-y-2">
          <p className="truncate font-display text-base font-semibold">{displayNameFor(user)}</p>
          <div className="flex flex-wrap gap-2">
            <Button
              type="button"
              variant="secondary"
              size="sm"
              onClick={() => fileInput.current?.click()}
              disabled={uploading}
            >
              <Camera size={14} aria-hidden="true" />
              {uploading ? "Uploading…" : showPicture ? "Change photo" : "Upload photo"}
            </Button>
            {showPicture && (
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={removePicture}
                disabled={removing}
              >
                <Trash size={14} aria-hidden="true" />
                {removing ? "Removing…" : "Remove photo"}
              </Button>
            )}
          </div>
          <p className="text-xs text-muted">PNG, JPEG, or WebP, up to 2 MB.</p>
          {/* Visually hidden and out of the tab order: the button above is the
              accessible control, so a bare file input would only add a
              mysterious focus stop. */}
          <input
            ref={fileInput}
            type="file"
            accept={AVATAR_ACCEPT}
            tabIndex={-1}
            className="sr-only"
            onChange={onPickFile}
          />
        </div>
      </div>

      <form onSubmit={saveProfile} className="mt-6 space-y-4">
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Field
            label="First name"
            autoComplete="given-name"
            maxLength={NAME_MAX_LENGTH}
            value={firstName}
            onChange={(e) => setFirstName(e.target.value)}
            aria-invalid={formError !== null}
            aria-describedby={formError ? FORM_ERROR_ID : undefined}
          />
          <Field
            label="Last name"
            autoComplete="family-name"
            maxLength={NAME_MAX_LENGTH}
            value={lastName}
            onChange={(e) => setLastName(e.target.value)}
            aria-invalid={formError !== null}
            aria-describedby={formError ? FORM_ERROR_ID : undefined}
          />
        </div>

        {formError && (
          <p id={FORM_ERROR_ID} className="text-xs text-error">
            {formError}
          </p>
        )}

        <Field
          label="Username"
          value={user?.username ?? ""}
          disabled
          hint="This is the name you sign in with, so it can't be changed."
        />

        <div className="space-y-3">
          <Field
            label="Email"
            type="email"
            value={user?.email ?? ""}
            disabled
            hint="Changing your address needs a fresh verification round trip, so it isn't available here yet — contact support to move your account to a new address."
          />

          {/* Verification status. Reachable when enforcement is suspended (no
              email transport configured) or when the account authenticated
              with an API key — in both cases the user CAN sign in while
              unverified, so this is the only in-app way to get another link. */}
          {user?.email_verified === false ? (
            <div className="flex flex-col gap-3 rounded-lg border border-warning/40 bg-warning/10 p-3 sm:flex-row sm:items-center sm:justify-between">
              <p className="flex items-start gap-2 text-sm text-on-background">
                <WarningCircle
                  size={18}
                  weight="fill"
                  className="mt-0.5 shrink-0 text-warning"
                  aria-hidden="true"
                />
                <span>
                  Your email address is not verified yet. Open the link we emailed you to
                  activate your account.
                </span>
              </p>
              <Button
                type="button"
                variant="secondary"
                className="shrink-0"
                onClick={resendVerification}
                disabled={resending}
              >
                {resending ? "Sending…" : "Resend link"}
              </Button>
            </div>
          ) : (
            <p className="flex items-center gap-2 text-sm text-success">
              <CheckCircle size={16} weight="fill" aria-hidden="true" />
              Email verified
            </p>
          )}
        </div>

        <div className="flex items-center gap-3">
          <Button type="submit" disabled={!changed || saving}>
            {saving ? "Saving…" : "Save changes"}
          </Button>
          {changed && !saving && <span className="text-xs text-muted">Unsaved changes</span>}
        </div>
      </form>
    </Card>
  );
}
