from .models import Profile


def get_profile(user):
    try:
        return Profile.objects.get(user=user)
    except Profile.DoesNotExist:
        return None


def profile_complete(profile):
    if profile is None:
        print("Profile is None")
        return False
    
    # Check if the profile image is the default placeholder
    is_default_image = profile.image.name == 'avatar.jpg' if profile.image else True
    
    conditions = [
        profile.first_name,
        profile.last_name,
        profile.date_birth,
        profile.Number_children is not None,  # Ensure this is not None
        profile.marital_status,
        profile.religion,
        profile.phone_number,
        profile.gender,
        not is_default_image  # Ensure image is not the default placeholder
    ]
    
    if not all(conditions):
        return False
    
    return True


def application_form_profile_complete(profile):
    if profile is None:
        return False

    is_default_image = profile.image.name == 'avatar.jpg' if profile.image else True

    conditions = [
        profile.first_name,
        profile.last_name,
        profile.date_birth,
        profile.marital_status,
        profile.religion,
        profile.phone_number,
        profile.gender,
        profile.nationality,
        profile.home_address,
        profile.state,
        profile.lga,
        not is_default_image,
    ]

    return all(conditions)


def profile_contact_complete(profile):
    if profile is None:
        return False
    return (
        profile.nationality and
        profile.home_address and
        profile.state and
        profile.lga
    )


