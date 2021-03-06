import mimetypes

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.http import Http404, StreamingHttpResponse
from django.shortcuts import get_object_or_404, render, redirect
from django.views.decorators.cache import cache_control
from django.views.decorators.clickjacking import xframe_options_sameorigin

from kuma.core.utils import is_untrusted
from kuma.core.decorators import login_required
from kuma.wiki.models import Document
from kuma.wiki.decorators import process_document_path

from .forms import AttachmentRevisionForm
from .models import Attachment
from .utils import allow_add_attachment_by, convert_to_http_date


# Mime types used on MDN
OVERRIDE_MIMETYPES = {
    'image/jpeg': '.jpeg, .jpg, .jpe',
    'image/vnd.adobe.photoshop': '.psd',
}

IMAGE_MIMETYPES = ['image/png', 'image/jpeg', 'image/jpg', 'image/gif']


def guess_extension(_type):
    return OVERRIDE_MIMETYPES.get(_type, mimetypes.guess_extension(_type))


def raw_file(request, attachment_id, filename):
    """
    Serve up an attachment's file.
    """
    qs = Attachment.objects.select_related('current_revision')
    attachment = get_object_or_404(qs, pk=attachment_id)
    if attachment.current_revision is None:
        raise Http404

    if is_untrusted(request):
        rev = attachment.current_revision

        @cache_control(public=True, max_age=60 * 15)
        def stream_raw_file(*args):
            if settings.DEBUG:
                # to work around an issue of the localdevstorage with streamed
                # files we'll have to read some of the file here first
                rev.file.read(rev.file.DEFAULT_CHUNK_SIZE)
            response = StreamingHttpResponse(rev.file,
                                             content_type=rev.mime_type)
            try:
                response['Content-Length'] = rev.file.size
            except OSError:
                pass
            response['Last-Modified'] = convert_to_http_date(rev.created)
            response['X-Frame-Options'] = 'ALLOW-FROM %s' % settings.DOMAIN
            return response

        return stream_raw_file(request)
    else:
        return redirect(attachment.get_file_url(), permanent=True)


def mindtouch_file_redirect(request, file_id, filename):
    """Redirect an old MindTouch file URL to a new kuma file URL."""
    attachment = get_object_or_404(Attachment, mindtouch_attachment_id=file_id)
    return redirect(attachment.get_file_url(), permanent=True)


@xframe_options_sameorigin
@login_required
@process_document_path
def edit_attachment(request, document_slug, document_locale):
    """
    Create a new Attachment object and populate its initial
    revision or show a separate form view that allows to fix form submission
    errors.

    Redirects back to the document's editing URL on success.
    """
    document = get_object_or_404(
        Document,
        locale=document_locale,
        slug=document_slug,
    )
    if request.method != 'POST':
        return redirect(document.get_edit_url())
    # No access if no permissions to upload
    if not allow_add_attachment_by(request.user):
        raise PermissionDenied

    form = AttachmentRevisionForm(data=request.POST, files=request.FILES)
    if form.is_valid():
        revision = form.save(commit=False)
        revision.creator = request.user
        attachment = Attachment.objects.create(title=revision.title)
        revision.attachment = attachment
        revision.save()
        # adding the attachment to the document's files (M2M)
        attachment.attach(document, request.user, revision)
        return redirect(document.get_edit_url())
    else:
        context = {
            'form': form,
            'document': document,
        }
        return render(request, 'attachments/edit_attachment.html', context)
