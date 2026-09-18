from rest_framework import serializers
from django.contrib.auth import authenticate, get_user_model
from django.db.models import Sum, Count

from farms.models import (
    Farm, FarmActivity, HarvestRecord, FarmExpense, SalesRecord,
    FarmProject, ProductionBatch, ProjectPlannedActivity, ProjectInputRecord, ProjectRevenueRecord,
)
from marketplace.models import ProduceListing, ProduceListingImage, BuyerRequest, BuyerRequestImage, ListingInquiry, MarketplacePurchase
from profiles.models import BusinessProfile

User = get_user_model()


def _phone_candidates(value: str) -> list[str]:
    """Return common Uganda phone formats so web and mobile login behave the same."""
    raw = str(value or "").strip().replace(" ", "")
    if not raw:
        return []
    candidates = {raw}
    digits = raw.replace("+", "")
    if digits.startswith("256") and len(digits) >= 12:
        candidates.add("0" + digits[3:])
        candidates.add("+" + digits)
    elif digits.startswith("0") and len(digits) >= 10:
        candidates.add("256" + digits[1:])
        candidates.add("+256" + digits[1:])
    elif len(digits) == 9:
        candidates.add("0" + digits)
        candidates.add("256" + digits)
        candidates.add("+256" + digits)
    return list(candidates)


def _find_user_by_identifier(identifier: str):
    identifier = str(identifier or "").strip()
    if not identifier:
        return None

    if "@" in identifier:
        user = User.objects.filter(email__iexact=identifier).first()
        if user:
            return user

    for phone in _phone_candidates(identifier):
        user = User.objects.filter(phone=phone).first()
        if user:
            return user

    try:
        return User.objects.filter(username__iexact=identifier).first()
    except Exception:
        return None


class AccountRegistrationSerializer(serializers.Serializer):
    full_name = serializers.CharField(max_length=255)
    password = serializers.CharField(write_only=True, min_length=6)
    phone = serializers.CharField(required=False, allow_blank=True)
    email = serializers.EmailField(required=False, allow_blank=True)
    district = serializers.CharField(required=False, allow_blank=True)
    account_type = serializers.ChoiceField(choices=[("farmer", "Farmer"), ("guest", "Guest")], default="farmer")

    def validate(self, attrs):
        phone = str(attrs.get("phone") or "").strip()
        email = str(attrs.get("email") or "").strip().lower()
        if not phone and not email:
            raise serializers.ValidationError("Provide either phone or email.")
        if phone:
            candidates = _phone_candidates(phone)
            phone = next((p for p in candidates if p.startswith("+256")), phone)
            if User.objects.filter(phone__in=candidates).exists():
                raise serializers.ValidationError({"phone": "This phone number is already registered."})
        if email and User.objects.filter(email__iexact=email).exists():
            raise serializers.ValidationError({"email": "This email is already registered."})
        attrs["phone"] = phone or None
        attrs["email"] = email or None
        return attrs

    def create(self, validated_data):
        from accounts.services import create_farmer_account

        password = validated_data.pop("password")
        account_type = validated_data.pop("account_type", "farmer")
        if account_type != "farmer":
            raise serializers.ValidationError({"account_type": "Only farmer registration is supported by this endpoint."})

        return create_farmer_account(
            full_name=validated_data.get("full_name"),
            password=password,
            phone=validated_data.get("phone"),
            email=validated_data.get("email"),
            district=validated_data.get("district"),
            verified=True,
        )


class FarmerLoginSerializer(serializers.Serializer):
    identifier = serializers.CharField(required=False, allow_blank=True)
    username = serializers.CharField(required=False, allow_blank=True)
    phone_or_email = serializers.CharField(required=False, allow_blank=True)
    phone = serializers.CharField(required=False, allow_blank=True)
    email = serializers.CharField(required=False, allow_blank=True)
    password = serializers.CharField(write_only=True)

    def validate(self, attrs):
        identifier = (
            attrs.get("identifier")
            or attrs.get("username")
            or attrs.get("phone_or_email")
            or attrs.get("phone")
            or attrs.get("email")
        )
        password = attrs.get("password")

        if not identifier or not password:
            raise serializers.ValidationError("Phone, email or username and password are required.")

        user_obj = _find_user_by_identifier(identifier)
        user = None

        if user_obj:
            login_key = getattr(user_obj, User.USERNAME_FIELD, None) or user_obj.email or user_obj.phone
            user = authenticate(self.context.get("request"), username=login_key, password=password)
            if user is None and user_obj.email:
                user = authenticate(self.context.get("request"), username=user_obj.email, password=password)
            if user is None and user_obj.phone:
                user = authenticate(self.context.get("request"), username=user_obj.phone, password=password)
            if user is None and user_obj.check_password(password):
                user = user_obj
        else:
            user = authenticate(self.context.get("request"), username=identifier, password=password)

        if not user:
            raise serializers.ValidationError("Invalid login credentials.")
        if not user.is_active:
            raise serializers.ValidationError("This account is inactive.")
        if getattr(user, "account_type", None) not in ["farmer", "guest", "admin"]:
            raise serializers.ValidationError("Only farmer or guest accounts can use this API.")

        attrs["user"] = user
        return attrs


class FarmerProfileSerializer(serializers.Serializer):
    """Read and update the logged-in farmer profile.

    Phone is intentionally read-only here because it can be used as a login
    identifier. A phone change should go through an OTP verification flow.
    """

    id = serializers.UUIDField(read_only=True)
    full_name = serializers.CharField(max_length=255, required=False)
    phone = serializers.CharField(read_only=True, allow_null=True)
    email = serializers.EmailField(required=False, allow_blank=True, allow_null=True)
    district = serializers.CharField(max_length=100, required=False, allow_blank=True, allow_null=True)
    account_type = serializers.CharField(read_only=True)
    is_verified = serializers.BooleanField(read_only=True)
    is_phone_verified = serializers.BooleanField(read_only=True)
    is_email_verified = serializers.BooleanField(read_only=True)

    national_id = serializers.CharField(max_length=50, required=False, allow_blank=True, allow_null=True)
    gender = serializers.ChoiceField(choices=["male", "female", "other"], required=False, allow_null=True)
    date_of_birth = serializers.DateField(required=False, allow_null=True)
    village = serializers.CharField(max_length=100, required=False, allow_blank=True, allow_null=True)
    subcounty = serializers.CharField(max_length=100, required=False, allow_blank=True, allow_null=True)
    primary_crop = serializers.CharField(max_length=100, required=False, allow_blank=True, allow_null=True)
    farming_experience_years = serializers.IntegerField(required=False, min_value=0)

    def to_representation(self, instance):
        data = {
            "id": instance.id,
            "full_name": instance.full_name,
            "phone": instance.phone,
            "email": instance.email,
            "district": instance.district,
            "account_type": instance.account_type,
            "is_verified": instance.is_verified,
            "is_phone_verified": instance.is_phone_verified,
            "is_email_verified": instance.is_email_verified,
        }
        profile = getattr(instance, "farmer_profile", None)
        data.update({
            "national_id": getattr(profile, "national_id", None),
            "gender": getattr(profile, "gender", None),
            "date_of_birth": getattr(profile, "date_of_birth", None),
            "village": getattr(profile, "village", None),
            "subcounty": getattr(profile, "subcounty", None),
            "primary_crop": getattr(profile, "primary_crop", None),
            "farming_experience_years": getattr(profile, "farming_experience_years", 0),
        })
        return super().to_representation(type("ProfileView", (), data)())

    def validate_email(self, value):
        if not value:
            return None
        value = value.strip().lower()
        user = self.instance
        qs = User.objects.filter(email__iexact=value)
        if user is not None:
            qs = qs.exclude(pk=user.pk)
        if qs.exists():
            raise serializers.ValidationError("This email is already registered.")
        return value

    def update(self, instance, validated_data):
        from profiles.models import FarmerProfile

        user_fields = {"full_name", "email", "district"}
        profile_fields = {
            "national_id", "gender", "date_of_birth", "village",
            "subcounty", "primary_crop", "farming_experience_years",
        }

        old_email = instance.email
        for field in user_fields:
            if field in validated_data:
                setattr(instance, field, validated_data[field])

        if "email" in validated_data and validated_data["email"] != old_email:
            instance.is_email_verified = False

        instance.save()

        profile, _ = FarmerProfile.objects.get_or_create(
            user=instance,
            defaults={"district": instance.district},
        )
        for field in profile_fields:
            if field in validated_data:
                setattr(profile, field, validated_data[field])
        if "district" in validated_data:
            profile.district = validated_data["district"]
        profile.save()
        return instance


class OfflineModelSerializer(serializers.ModelSerializer):
    client_id = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    client_updated_at = serializers.DateTimeField(required=False, allow_null=True)
    sync_status = serializers.CharField(read_only=True)
    is_deleted = serializers.BooleanField(required=False)


class FarmerOwnedModelSerializer(OfflineModelSerializer):
    """Base serializer for models that belong to the logged-in farmer.

    It attaches request.user before validation/save so API creates do not hit
    `RelatedObjectDoesNotExist: <Model> has no farmer`, and it prevents mobile
    clients from posting another farmer's farm/project IDs.
    """
    farmer = serializers.HiddenField(default=serializers.CurrentUserDefault())

    def validate(self, attrs):
        attrs = super().validate(attrs)
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if not user or not getattr(user, "is_authenticated", False):
            return attrs

        farm = attrs.get("farm") or getattr(self.instance, "farm", None)
        project = attrs.get("project") or getattr(self.instance, "project", None)
        harvest = attrs.get("harvest") or getattr(self.instance, "harvest", None)
        batch = attrs.get("batch") or getattr(self.instance, "batch", None)

        if batch:
            if getattr(batch, "farmer_id", None) != user.id:
                raise serializers.ValidationError({"batch": "This batch does not belong to your account."})
            attrs["project"] = batch.project
            attrs["farm"] = batch.farm
            project = batch.project
            farm = batch.farm

        if project and not farm:
            attrs["farm"] = project.farm
            farm = project.farm

        if harvest:
            if getattr(harvest, "farmer_id", None) != user.id:
                raise serializers.ValidationError({"harvest": "This harvest does not belong to your account."})
            if not project and getattr(harvest, "project", None):
                attrs["project"] = harvest.project
                project = harvest.project
            if not farm and getattr(harvest, "farm", None):
                attrs["farm"] = harvest.farm
                farm = harvest.farm

        if farm and getattr(farm, "farmer_id", None) != user.id:
            raise serializers.ValidationError({"farm": "This farm does not belong to your account."})
        if project and getattr(project, "farmer_id", None) != user.id:
            raise serializers.ValidationError({"project": "This project does not belong to your account."})
        if project and farm and getattr(project, "farm_id", None) != getattr(farm, "id", None):
            raise serializers.ValidationError({"project": "The selected project must belong to the selected farm."})
        if batch and project and getattr(batch, "project_id", None) != getattr(project, "id", None):
            raise serializers.ValidationError({"batch": "The selected batch must belong to the selected project."})
        return attrs

    def create(self, validated_data):
        request = self.context.get("request")
        if request and getattr(request, "user", None) and getattr(request.user, "is_authenticated", False):
            validated_data["farmer"] = request.user
        return super().create(validated_data)

    def update(self, instance, validated_data):
        request = self.context.get("request")
        if request and getattr(request, "user", None) and getattr(request.user, "is_authenticated", False):
            validated_data["farmer"] = request.user
        return super().update(instance, validated_data)


class FarmSerializer(FarmerOwnedModelSerializer):
    class Meta:
        model = Farm
        fields = "__all__"
        read_only_fields = ["created_at", "updated_at", "sync_status"]


class ProjectLinkedSerializer(FarmerOwnedModelSerializer):
    class Meta:
        abstract = True
        extra_kwargs = {
            "farm": {"required": False, "allow_null": True},
        }


class FarmActivitySerializer(ProjectLinkedSerializer):
    total_cost = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)

    class Meta:
        model = FarmActivity
        fields = "__all__"
        read_only_fields = ["created_at", "updated_at", "sync_status", "total_cost"]
        extra_kwargs = {"farm": {"required": False, "allow_null": True}}


class HarvestRecordSerializer(ProjectLinkedSerializer):
    class Meta:
        model = HarvestRecord
        fields = "__all__"
        read_only_fields = ["created_at", "updated_at", "sync_status"]
        extra_kwargs = {"farm": {"required": False, "allow_null": True}}


class FarmExpenseSerializer(ProjectLinkedSerializer):
    farm_name = serializers.CharField(source="farm.farm_name", read_only=True)
    project_name = serializers.CharField(source="project.name", read_only=True)
    batch_name = serializers.CharField(source="batch.display_name", read_only=True)

    class Meta:
        model = FarmExpense
        fields = "__all__"
        read_only_fields = ["created_at", "updated_at", "sync_status"]
        extra_kwargs = {"farm": {"required": False, "allow_null": True}}


class SalesRecordSerializer(ProjectLinkedSerializer):
    farm_name = serializers.CharField(source="farm.farm_name", read_only=True)
    project_name = serializers.CharField(source="project.name", read_only=True)
    batch_name = serializers.CharField(source="batch.display_name", read_only=True)

    class Meta:
        model = SalesRecord
        fields = "__all__"
        read_only_fields = ["total_amount", "created_at", "updated_at", "sync_status"]
        extra_kwargs = {"farm": {"required": False, "allow_null": True}}


class FarmProjectSerializer(FarmerOwnedModelSerializer):
    farm_name = serializers.CharField(source="farm.farm_name", read_only=True)
    planned_profit = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    actual_cost = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    actual_revenue = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    estimated_profit = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    projected_profit = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    cost_variance = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)

    class Meta:
        model = FarmProject
        fields = "__all__"
        read_only_fields = [
            "created_at", "updated_at", "sync_status",
            "planned_profit", "actual_cost", "actual_revenue",
            "estimated_profit", "projected_profit", "cost_variance",
        ]


class ProductionBatchSerializer(FarmerOwnedModelSerializer):
    farm_name = serializers.CharField(source="farm.farm_name", read_only=True)
    project_name = serializers.CharField(source="project.name", read_only=True)
    display_name = serializers.CharField(read_only=True)
    actual_expenses = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    actual_revenue = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    profit = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    harvested_quantity = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    sold_quantity = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    stock_balance = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)

    class Meta:
        model = ProductionBatch
        fields = "__all__"
        read_only_fields = [
            "created_at", "updated_at", "sync_status", "display_name",
            "actual_expenses", "actual_revenue", "profit", "harvested_quantity",
            "sold_quantity", "stock_balance",
        ]


class ProjectPlannedActivitySerializer(ProjectLinkedSerializer):
    cost_variance = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)

    class Meta:
        model = ProjectPlannedActivity
        fields = "__all__"
        read_only_fields = ["created_at", "updated_at", "sync_status", "cost_variance"]
        extra_kwargs = {"farm": {"required": False, "allow_null": True}}


class ProjectInputRecordSerializer(ProjectLinkedSerializer):
    class Meta:
        model = ProjectInputRecord
        fields = "__all__"
        read_only_fields = ["total_cost", "created_at", "updated_at", "sync_status"]
        extra_kwargs = {"farm": {"required": False, "allow_null": True}}


class ProjectRevenueRecordSerializer(ProjectLinkedSerializer):
    class Meta:
        model = ProjectRevenueRecord
        fields = "__all__"
        read_only_fields = ["amount", "created_at", "updated_at", "sync_status"]
        extra_kwargs = {"farm": {"required": False, "allow_null": True}}


class ProduceListingImageSerializer(serializers.ModelSerializer):
    image_url = serializers.SerializerMethodField()

    class Meta:
        model = ProduceListingImage
        fields = ["id", "image", "image_url", "is_primary", "sort_order", "created_at", "updated_at"]
        read_only_fields = ["id", "image_url", "created_at", "updated_at"]

    def get_image_url(self, obj):
        if not obj.image:
            return None
        url = obj.image.url
        request = self.context.get("request")
        return request.build_absolute_uri(url) if request else url


class ProduceListingSerializer(serializers.ModelSerializer):
    farm_name = serializers.CharField(source="farm.farm_name", read_only=True)
    farmer_name = serializers.CharField(source="farmer.full_name", read_only=True)
    pin_type = serializers.SerializerMethodField()
    images = ProduceListingImageSerializer(many=True, read_only=True)
    uploaded_images = serializers.ListField(
        child=serializers.ImageField(),
        write_only=True,
        required=False,
        allow_empty=True,
        help_text="Upload one or more product images using multipart/form-data. Use field name uploaded_images.",
    )
    primary_image_url = serializers.SerializerMethodField()

    def get_pin_type(self, obj):
        return "farmer"

    def get_primary_image_url(self, obj):
        image = None
        try:
            image = obj.images.filter(is_primary=True).first() or obj.images.first()
        except Exception:
            image = None
        if not image or not image.image:
            return None
        url = image.image.url
        request = self.context.get("request")
        return request.build_absolute_uri(url) if request else url

    class Meta:
        model = ProduceListing
        fields = "__all__"
        read_only_fields = ["farmer", "created_at", "updated_at"]

    def validate(self, attrs):
        attrs = super().validate(attrs)
        request = self.context.get("request")
        user = getattr(request, "user", None)
        farm = attrs.get("farm") or getattr(self.instance, "farm", None)
        if farm and user and getattr(user, "is_authenticated", False) and getattr(farm, "farmer_id", None) != user.id:
            raise serializers.ValidationError({"farm": "This farm does not belong to your account."})
        return attrs

    def _create_images(self, listing, images):
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if not images or not user or not getattr(user, "is_authenticated", False):
            return
        already_has_primary = listing.images.filter(is_primary=True).exists()
        start_order = listing.images.count()
        for index, image in enumerate(images):
            ProduceListingImage.objects.create(
                listing=listing,
                uploaded_by=user,
                image=image,
                is_primary=(not already_has_primary and index == 0),
                sort_order=start_order + index,
            )

    def create(self, validated_data):
        images = validated_data.pop("uploaded_images", [])
        listing = super().create(validated_data)
        self._create_images(listing, images)
        return listing

    def update(self, instance, validated_data):
        images = validated_data.pop("uploaded_images", [])
        listing = super().update(instance, validated_data)
        self._create_images(listing, images)
        return listing


class BuyerRequestImageSerializer(serializers.ModelSerializer):
    image_url = serializers.SerializerMethodField()

    class Meta:
        model = BuyerRequestImage
        fields = ["id", "image", "image_url", "is_primary", "sort_order", "created_at", "updated_at"]
        read_only_fields = ["id", "image_url", "created_at", "updated_at"]

    def get_image_url(self, obj):
        if not obj.image:
            return None
        url = obj.image.url
        request = self.context.get("request")
        return request.build_absolute_uri(url) if request else url


class BuyerRequestSerializer(serializers.ModelSerializer):
    buyer_name = serializers.CharField(source="business_user.full_name", read_only=True)
    buyer_phone = serializers.CharField(source="business_user.phone", read_only=True)
    buyer_email = serializers.EmailField(source="business_user.email", read_only=True)
    company_id = serializers.SerializerMethodField()
    company_name = serializers.SerializerMethodField()
    company_type = serializers.SerializerMethodField()
    company_district = serializers.SerializerMethodField()
    company_address = serializers.SerializerMethodField()
    price_range = serializers.SerializerMethodField()
    needed_summary = serializers.SerializerMethodField()
    location_summary = serializers.SerializerMethodField()
    primary_image_url = serializers.SerializerMethodField()
    images = BuyerRequestImageSerializer(many=True, read_only=True)
    pin_type = serializers.SerializerMethodField()

    def get_pin_type(self, obj):
        return "buyer"

    def _business_profile(self, obj):
        return getattr(obj.business_user, "business_profile", None)

    def get_company_id(self, obj):
        profile = self._business_profile(obj)
        return str(profile.id) if profile else None

    def get_company_name(self, obj):
        profile = self._business_profile(obj)
        return profile.business_name if profile else getattr(obj.business_user, "full_name", "Buyer")

    def get_company_type(self, obj):
        profile = self._business_profile(obj)
        return profile.business_type if profile else None

    def get_company_district(self, obj):
        profile = self._business_profile(obj)
        return profile.district if profile else None

    def get_company_address(self, obj):
        profile = self._business_profile(obj)
        return profile.physical_address if profile else None

    def get_price_range(self, obj):
        if obj.min_price and obj.max_price:
            return f"{obj.min_price} - {obj.max_price} per {obj.unit}"
        if obj.max_price:
            return f"Up to {obj.max_price} per {obj.unit}"
        if obj.min_price:
            return f"From {obj.min_price} per {obj.unit}"
        return "Price negotiable"

    def get_needed_summary(self, obj):
        return f"{obj.quantity_needed} {obj.unit} of {obj.crop_name}"

    def get_location_summary(self, obj):
        return obj.delivery_location or obj.delivery_district or "Location not specified"

    def get_primary_image_url(self, obj):
        image = None
        try:
            image = obj.images.filter(is_primary=True).first() or obj.images.first()
        except Exception:
            image = None
        if not image or not image.image:
            return None
        url = image.image.url
        request = self.context.get("request")
        return request.build_absolute_uri(url) if request else url

    class Meta:
        model = BuyerRequest
        fields = "__all__"
        read_only_fields = ["business_user", "created_at", "updated_at"]


class ListingInquirySerializer(serializers.ModelSerializer):
    class Meta:
        model = ListingInquiry
        fields = "__all__"
        read_only_fields = ["business_user", "created_at", "updated_at", "status"]


class CompanySerializer(serializers.ModelSerializer):
    name = serializers.CharField(source="business_name", read_only=True)
    phone = serializers.CharField(source="user.phone", read_only=True)
    email = serializers.EmailField(source="user.email", read_only=True)
    description = serializers.SerializerMethodField()
    active_buy_requests_count = serializers.SerializerMethodField()
    active_products_in_demand = serializers.SerializerMethodField()
    total_quantity_in_demand = serializers.SerializerMethodField()

    class Meta:
        model = BusinessProfile
        fields = [
            "id", "name", "business_name", "business_type", "contact_person",
            "district", "physical_address", "website", "phone", "email",
            "approval_status", "description", "active_buy_requests_count",
            "active_products_in_demand", "total_quantity_in_demand",
        ]

    def _open_requests(self, obj):
        return obj.user.buyer_requests.filter(status="open")

    def get_description(self, obj):
        products = list(self._open_requests(obj).values_list("crop_name", flat=True).distinct()[:4])
        if products:
            return "Buying " + ", ".join(products)
        parts = [obj.business_type, obj.district]
        return " • ".join([p for p in parts if p]) or "Agricultural buyer"

    def get_active_buy_requests_count(self, obj):
        return self._open_requests(obj).count()

    def get_active_products_in_demand(self, obj):
        return list(self._open_requests(obj).values_list("crop_name", flat=True).distinct())

    def get_total_quantity_in_demand(self, obj):
        total = self._open_requests(obj).aggregate(total=Sum("quantity_needed"))["total"]
        return total or 0


class MarketplacePurchaseSerializer(serializers.ModelSerializer):
    buyer_name = serializers.CharField(source="buyer.full_name", read_only=True)
    listing_name = serializers.CharField(source="listing.crop_name", read_only=True)
    farmer_name = serializers.CharField(source="listing.farmer.full_name", read_only=True)

    class Meta:
        model = MarketplacePurchase
        fields = "__all__"
        read_only_fields = ["buyer", "total_amount", "status", "created_at", "updated_at"]
