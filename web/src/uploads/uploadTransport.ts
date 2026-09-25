/**
 * The one transport for upload bytes: XMLHttpRequest.
 *
 * `fetch` cannot report upload progress — there is no request-side analogue of
 * a response body stream that a browser exposes with a byte count — so the
 * whole point of this feature (a real progress bar) rules it out. XHR's
 * `upload.onprogress` is still the only broadly supported way to observe bytes
 * leaving the browser.
 *
 * This is deliberately a *new* transport rather than a change to
 * `client.putToPresignedUrl`, which stays on `fetch` for the convert and guest
 * flows: their behaviour is not part of this feature and rewriting their
 * transport would put them at risk for no gain.
 */

export interface PutResult {
  /**
   * The object store's `ETag` header, or `null` when it was not exposed.
   *
   * The bucket CORS policy must list `ETag` in `Access-Control-Expose-Headers`
   * for the browser to hand it back; without that a multipart upload cannot be
   * completed, which is why the caller surfaces the absence rather than
   * silently sending an empty part list.
   */
  etag: string | null;
}

export interface PutOptions {
  signal?: AbortSignal;
  /** Called with cumulative bytes sent, and the request's own total. */
  onProgress?: (loaded: number, total: number) => void;
}

/** A `DOMException`-shaped abort rejection, so callers can branch on `name`. */
export function abortError(): Error {
  if (typeof DOMException === "function") {
    return new DOMException("Upload aborted", "AbortError");
  }
  const error = new Error("Upload aborted");
  error.name = "AbortError";
  return error;
}

/** True for an abort raised by {@link putWithXhr} or an `AbortSignal`. */
export function isAbortError(error: unknown): boolean {
  return error instanceof Error && error.name === "AbortError";
}

/**
 * PUT a blob to a presigned URL, reporting progress, and return its `ETag`.
 *
 * `Content-Type` is deliberately never set: the URL is signed without one and
 * B2/S3 reject a request that supplies a different header set with
 * `SignatureDoesNotMatch`. The same rule applies to part PUTs.
 *
 * Resolves on any 2xx. A non-2xx response, a network failure, or an abort
 * rejects — the caller decides whether that is worth retrying.
 */
export function putWithXhr(url: string, body: Blob, options: PutOptions = {}): Promise<PutResult> {
  const { signal, onProgress } = options;

  return new Promise<PutResult>((resolve, reject) => {
    if (signal?.aborted) {
      reject(abortError());
      return;
    }

    const xhr = new XMLHttpRequest();
    xhr.open("PUT", url);
    // `mode: "cors"` equivalent: the request is cross-origin to the object
    // store and must be a normal CORS request, because a `no-cors` response is
    // opaque and its headers (including `ETag`) are unreadable.
    xhr.responseType = "text";

    const onAbort = () => xhr.abort();
    signal?.addEventListener("abort", onAbort, { once: true });
    const cleanup = () => signal?.removeEventListener("abort", onAbort);

    if (onProgress) {
      xhr.upload.onprogress = (event: ProgressEvent) => {
        // `event.total` is the length of *this* request, not the whole file —
        // the caller aggregates when it is uploading parts.
        onProgress(event.loaded, event.total || body.size);
      };
    }

    xhr.onload = () => {
      cleanup();
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve({ etag: xhr.getResponseHeader("ETag") });
        return;
      }
      if (xhr.status === 403) {
        reject(
          new Error(
            "Upload was rejected by the object store (403). This is usually a presigned-URL signature mismatch or a bucket CORS/permission issue.",
          ),
        );
        return;
      }
      reject(new Error(`Upload failed (${xhr.status || 0})`));
    };

    xhr.onerror = () => {
      cleanup();
      reject(
        new Error(
          "The object store blocked the upload (CORS). Configure the bucket CORS policy to allow this origin and the PUT method.",
        ),
      );
    };

    xhr.onabort = () => {
      cleanup();
      reject(abortError());
    };

    xhr.ontimeout = () => {
      cleanup();
      reject(new Error("Upload timed out."));
    };

    xhr.send(body);
  });
}
