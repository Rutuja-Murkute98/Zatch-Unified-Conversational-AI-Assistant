"""
Permanent test suite for bargains_repo.py - the signature feature.
Deliberately targets the ONE real bargain with a counter-offer (rather
than leaving it to chance) plus the wrong-user security guarantee.
"""

from app.repos import bargains_repo


class TestCheckBargainEligibility:
    async def test_returns_settings_for_bargainable_product(self, real_bargain_with_counter_offer):
        result = await bargains_repo.check_bargain_eligibility(
            str(real_bargain_with_counter_offer["productId"])
        )
        assert result["bargainingAllowed"] is True


class TestGetBargainStatus:
    async def test_returns_real_bargain_by_id(self, real_bargain_with_counter_offer):
        user_id = str(real_bargain_with_counter_offer["buyerId"])
        result = await bargains_repo.get_bargain_status(
            user_id, bargain_id=str(real_bargain_with_counter_offer["_id"])
        )
        assert result is not None
        assert result["hasCounterOffer"] is True

    async def test_lookup_by_product_id_also_works(self, real_bargain_with_counter_offer):
        user_id = str(real_bargain_with_counter_offer["buyerId"])
        result = await bargains_repo.get_bargain_status(
            user_id, product_id=str(real_bargain_with_counter_offer["productId"])
        )
        assert result is not None

    async def test_wrong_user_cannot_see_bargain(self, real_bargain_with_counter_offer):
        wrong_user_id = "000000000000000000000000"
        result = await bargains_repo.get_bargain_status(
            wrong_user_id, bargain_id=str(real_bargain_with_counter_offer["_id"])
        )
        assert result is None, "SECURITY: a real bargain must be invisible to the wrong user"


class TestGetCounterOffer:
    async def test_real_counter_offer_returns_details(self, real_bargain_with_counter_offer):
        user_id = str(real_bargain_with_counter_offer["buyerId"])
        result = await bargains_repo.get_counter_offer(
            user_id, bargain_id=str(real_bargain_with_counter_offer["_id"])
        )
        assert result["counterOfferExists"] is True
        assert result["price"] is not None

    async def test_no_counter_offer_handled_gracefully(self, real_bargain_without_counter_offer):
        user_id = str(real_bargain_without_counter_offer["buyerId"])
        result = await bargains_repo.get_counter_offer(
            user_id, bargain_id=str(real_bargain_without_counter_offer["_id"])
        )
        assert result["counterOfferExists"] is False