def complete_b_score(candidate_id, product_url):
    factor_names = ("exposure_duplicates", "shop_strength", "image_investment",
                    "promotion_strength", "review_freshness", "keyword_exposure")
    return {"scoring_method": "legacy-heat-6-factor-v1", "candidate_id": candidate_id,
            "product_url": product_url, "observed_at": "2026-09-25T13:00:00+09:00",
            "score": 6, "confirmed_max": 12, "full_max": 12, "confirmed_factors": 6,
            "unconfirmed_factors": [], "grade": "B", "provisional_grade": "B", "status": "complete",
            "factors": {name: {"points": 1, "status": "scored", "reason": "確認済み", "evidence": {}}
                        for name in factor_names}}
