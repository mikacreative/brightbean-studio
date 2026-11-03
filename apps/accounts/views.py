from django.contrib import messages
from django.contrib.auth import logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST


def health_check(request):
    """Health check endpoint at /health/."""
    return JsonResponse({"status": "ok"})


@login_required
def dashboard(request):
    """Main dashboard - redirects to last used workspace or shows org overview."""
    from apps.members.models import WorkspaceMembership

    user = request.user

    # Only trust last_workspace_id if the user still has an active membership
    # in that workspace. Otherwise the org may have been deleted out from under
    # them and we'd redirect into a 403.
    if user.last_workspace_id:
        if WorkspaceMembership.objects.filter(
            user=user,
            workspace_id=user.last_workspace_id,
            workspace__is_archived=False,
        ).exists():
            return redirect("calendar:calendar", workspace_id=user.last_workspace_id)
        user.last_workspace_id = None
        user.save(update_fields=["last_workspace_id"])

    # Fallback: try to find any workspace the user belongs to
    membership = (
        WorkspaceMembership.objects.filter(user=user, workspace__is_archived=False).select_related("workspace").first()
    )
    if membership:
        user.last_workspace_id = membership.workspace.id
        user.save(update_fields=["last_workspace_id"])
        return redirect("calendar:calendar", workspace_id=membership.workspace.id)

    return render(request, "accounts/dashboard.html")


@login_required
@require_http_methods(["GET", "POST"])
def account_settings(request):
    user = request.user
    tab = request.GET.get("tab", "profile")
    settings_active = "preferences" if tab == "preferences" else "profile"

    if request.method == "POST":
        action = request.POST.get("action", "")

        if action == "update_photo":
            _handle_photo_update(request, user)
        elif action == "update_name":
            _handle_name_update(request, user)
        elif action == "update_password":
            _handle_password_update(request, user)
        elif action == "delete_account":
            return _handle_account_deletion(request, user)

        return redirect("accounts:settings")

    # Fetch the user's organization membership for role display
    from apps.members.models import OrgMembership

    org_membership = OrgMembership.objects.filter(user=user).select_related("organization").first()

    return render(
        request,
        "accounts/settings.html",
        {
            "settings_active": settings_active,
            "org_membership": org_membership,
        },
    )


def _handle_photo_update(request, user):
    """Handle avatar upload or deletion."""
    # Handle deletion
    if request.POST.get("delete_photo") == "1":
        if user.avatar:
            user.avatar.delete(save=False)
        # Named fields only: this instance was loaded at the start of the
        # request, and a full save would write back any column another request
        # changed meanwhile (e.g. review_banner_dismissed_at).
        user.save(update_fields=["avatar", "updated_at"])
        messages.success(request, "头像已移除。")
        return

    # Handle upload
    if "avatar" not in request.FILES:
        return

    avatar = request.FILES["avatar"]

    # Validate file type
    allowed_types = ("image/jpeg", "image/png", "image/webp", "image/gif")
    if avatar.content_type not in allowed_types:
        messages.error(request, "头像须为 JPEG、PNG、WebP 或 GIF 图片。")
        return

    # Validate file size (2 MB max)
    max_size = 2 * 1024 * 1024
    if avatar.size > max_size:
        messages.error(request, "头像大小须小于 2 MB。")
        return

    # Validate minimum dimensions (180x180)
    #
    # PIL is imported here rather than at module scope because config.urls
    # imports this module for ``health_check``, so a top-level import loaded
    # Pillow's _imaging extension into every web AND worker process at boot,
    # whether or not an avatar was ever touched. Matches how the rest of the
    # codebase imports PIL (apps.media_library.services, apps.intelligence.views).
    from PIL import Image

    try:
        with Image.open(avatar) as img:
            width, height = img.size
        if width < 180 or height < 180:
            messages.error(request, "头像尺寸至少为 180×180 像素。")
            return
    except Exception:
        messages.error(request, "无法读取图片文件。")
        return
    finally:
        avatar.seek(0)  # Reset file pointer after reading

    # Delete old avatar before saving new one
    if user.avatar:
        user.avatar.delete(save=False)

    user.avatar = avatar
    user.save(update_fields=["avatar", "updated_at"])
    messages.success(request, "头像已更新。")


def _handle_name_update(request, user):
    """Handle name change."""
    name = request.POST.get("name", "").strip()
    if not name:
        messages.error(request, "名称不能为空。")
        return

    user.name = name
    user.save(update_fields=["name"])
    messages.success(request, "名称已更新。")


def _handle_password_update(request, user):
    """Handle password change."""
    current_password = request.POST.get("current_password", "")
    password = request.POST.get("password", "")
    password_confirm = request.POST.get("password_confirm", "")

    if not current_password:
        messages.error(request, "请输入当前密码。")
        return

    if not user.check_password(current_password):
        messages.error(request, "当前密码不正确。")
        return

    if not password:
        messages.error(request, "新密码不能为空。")
        return

    if len(password) < 8:
        messages.error(request, "新密码至少需要 8 个字符。")
        return

    if password != password_confirm:
        messages.error(request, "两次输入的新密码不一致。")
        return

    user.set_password(password)
    user.save(update_fields=["password", "updated_at"])
    update_session_auth_hash(request, user)
    messages.success(request, "密码已修改。")


def _handle_account_deletion(request, user):
    """Handle account deletion with sole-owner safety check."""
    from apps.members.models import OrgMembership

    # Check if user is the sole owner of any organization
    owned_memberships = OrgMembership.objects.filter(user=user, org_role=OrgMembership.OrgRole.OWNER).select_related(
        "organization"
    )

    sole_owner_orgs = []
    for membership in owned_memberships:
        other_owners = (
            OrgMembership.objects.filter(
                organization=membership.organization,
                org_role=OrgMembership.OrgRole.OWNER,
            )
            .exclude(user=user)
            .exists()
        )
        if not other_owners:
            sole_owner_orgs.append(membership.organization.name)

    if sole_owner_orgs:
        org_names = ", ".join(sole_owner_orgs)
        messages.error(
            request,
            f"你是以下组织的唯一所有者：{org_names}。请先转移所有权或删除组织，再删除你的账号。",
        )
        return redirect("accounts:settings")

    # Safe to delete
    user.delete()
    logout(request)
    messages.success(request, "你的账号已删除。")
    return redirect("account_login")


@login_required
@require_http_methods(["GET", "POST"])
def accept_terms(request):
    """Terms of Service acceptance page for social signup users."""
    if request.user.tos_accepted_at is not None:
        return redirect("/")

    if request.method == "POST":
        if request.POST.get("agree"):
            request.user.tos_accepted_at = timezone.now()
            request.user.save(update_fields=["tos_accepted_at"])
            return redirect("/")
        messages.error(request, "请先同意服务条款和隐私政策。")

    return render(request, "account/accept_terms.html")


def logout_view(request):
    logout(request)
    return redirect("account_login")


@login_required
@require_POST
def dismiss_review_banner(request):
    """Hide the sidebar's Trustpilot card until the next login."""
    request.user.review_banner_dismissed_at = timezone.now()
    request.user.save(update_fields=["review_banner_dismissed_at"])
    return HttpResponse(status=204)
