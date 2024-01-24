class UserOrderViewset(viewsets.ModelViewSet):
    queryset = Order.objects
    serializer_class = OrderSerializer
    http_method_names = ["get", "post", "put"]
    permission_classes = [permissions.IsAuthenticated]

    def get_serializer_class(self):
        if self.action == 'retrieve':
            return AdvanceOrderSerializer
        
        return super().get_serializer_class()

    def get_queryset(self):
        in_process = self.queryset.filter(order_user=self.request.user, order_status=0, is_paid=False)
        in_process.delete()

        if self.request.user.is_deleted:
            return []
        
        return self.queryset.filter(order_user=self.request.user, is_deleted=False).order_by("-id")
    
    def create(self, request):
        response_data = {"status": status.HTTP_400_BAD_REQUEST, "message": "", "data": {}}
        
        if request.user.is_deleted:
            response_data["status"] = status.HTTP_404_NOT_FOUND
            response_data["message"] = "User has been deleted"
            return Response(response_data, status=response_data["status"])
        
        general = GeneralSetting.objects.all().first()
        cart_objs = Cart.objects.filter(user=request.user)
        if not cart_objs:
            response_data["message"] = "Your cart is empty! Add some items to cart"
            return Response(response_data, status=response_data["status"])

        restaurant = cart_objs.first().restaurant
        offer = None

        try:
            address = UserAddress.objects.get(user=request.user, id=request.data["address"])
            order_notes = request.data.get("order_notes", None)
            payment_mode = request.data.get("payment_mode", None)
            if request.data["offer"]:
                offer = Offer.objects.get(id=request.data["offer"], is_active=True)

        except serializers.ValidationError as e:
            errors = {key: value[0] for key, value in e.detail.items()}
            response_data['message'] = errors
            return Response(response_data, status=response_data["status"])

        except Exception as e:
            response_data['message'] = str(e)
            return Response(response_data, status=response_data["status"])

        
        commission_rate_type = 1
        commission_rate = 0

        if restaurant.user.is_commission:
            commission_rate_type = restaurant.user.commission_rate_type
            commission_rate = restaurant.user.commission_rate
        else:
            commission_rate_type = general.commission_rate_type
            commission_rate = general.commission_rate
    

        order_cost = 0
        for item in cart_objs:
            order_cost += item.total_price

        offer_cost = 0
        if offer:
            if offer.offer_type == 1:
                offer_cost = tax_calculator(offer.rate, order_cost)
            else:
                offer_cost = offer.rate

        order_cost_discounted = order_cost - offer_cost
        
        tax_objects = Tax.objects.filter(restaurant=restaurant, is_active=True)
        total_tax_rate = tax_objects.aggregate(Sum("percentage"))["percentage__sum"]
        total_tax_rate = total_tax_rate or 0
        total_tax_cost = tax_calculator(total_tax_rate, order_cost_discounted)

        delivery_obj = Delivery.objects.filter(distance_upto__gte=0).order_by("distance_upto").first()
        delivery_cost = 0
        if delivery_obj:
            if delivery_obj.rate_type == 1:
                delivery_cost = tax_calculator(delivery_obj.rate, order_cost_discounted)
            else:
                delivery_cost = delivery_obj.rate

        order_final_cost = order_cost_discounted + total_tax_cost + delivery_cost

        order_obj = Order(
            order_user=request.user, offer=offer, order_cost=order_cost, total_tax_cost=total_tax_cost,
            shipping_cost=delivery_cost, discount_amount=offer_cost, order_notes=order_notes,
            order_final_cost=order_final_cost, user=restaurant.user,
            commission_rate_type=commission_rate_type, commission_rate=commission_rate
        )
        order_obj.save()

        restaurant.clone_to_order_restaurant(order_obj)
        address.clone_to_order_address(order_obj)
        for tax in tax_objects:
            tax.clone_to_order_tax(order_obj)

        for item in cart_objs:
            item_instance = item.item.clone_to_order_item(order_obj)
            item_instance.quantity = item.item_count
            item_instance.save()

            for add_on in item.add_on_data:
                add_on.item_addon.clone_to_order_item_addon(item_instance)

        if payment_mode and payment_mode == "COD":
            order_obj.payment_mode = "COD"
            order_obj.order_status = 1
            order_obj.save()

            response_data["status"] = status.HTTP_201_CREATED
            response_data["message"] = "Order has been created!"

            response_data["data"]['payment_type'] = "COD"
            response_data["data"]["payment"] = None
            response_data["data"]["order"] = self.serializer_class(order_obj).data
            order_obj.save()

            cart_objs.delete()

            notification_message = f"{order_obj.get_order_status} for order id #{order_obj.id}"
            notification(
                request=request,
                order_id=order_obj.id,
                notification_for=3,
                notification_type=2,
                title=order_obj.get_order_status,
                message=notification_message
            )

            msg = SendMail({"order": order_obj})
            msg.order(self.request.user.email)

            return Response(response_data, status=response_data['status'])

        if "is_wallet" in request.data:
            if request.data['is_wallet'] == True:
                order_obj.wallet_amount = order_obj.order_final_cost

                if request.user.wallet < order_obj.order_final_cost:
                    order_obj.wallet_amount = request.user.wallet

                order_final_cost = Decimal(order_obj.order_final_cost) - request.user.wallet
                order_obj.is_wallet = True

                if order_final_cost <= 0 :
                    request.user.wallet = request.user.wallet - Decimal(order_obj.order_final_cost)
                    request.user.save()
                    order_obj.payment_mode = "WL"
                    order_obj.is_paid = True
                    order_obj.order_status = 1
                    order_obj.save()

                    message = f"Order has been created. Amount {general.currency_symbol} {('%.2f' % order_obj.order_final_cost)} has been deducted from wallet."
                    Transaction().booking(request, order_obj.id, 1, message, order_obj.order_final_cost)

                    response_data["status"] = status.HTTP_201_CREATED
                    response_data["message"] = "Order has been created!"

                    response_data["data"]['payment_type'] = "wallet"
                    response_data["data"]["payment"] = None
                    response_data["data"]["order"] = self.serializer_class(order_obj).data
                    order_obj.save()

                    cart_objs.delete()

                    notification_message = f"{order_obj.get_order_status} for order id #{order_obj.id}"
                    notification(
                        request=request,
                        order_id=order_obj.id,
                        notification_for=3,
                        notification_type=2,
                        title=order_obj.get_order_status,
                        message=notification_message
                    )

                    msg = SendMail({"order": order_obj})
                    msg.order(self.request.user.email)

                    return Response(response_data, status=response_data['status']) 

        client = razorpay.Client(auth=(razor_id, razor_secrect_key))
        payment = client.order.create(
            {"amount":int(order_final_cost*100), "currency":general.currency_name, "payment_capture":"1"}
        )
        order_obj.order_id = payment["id"]
        order_obj.save()

        response_data["status"] = status.HTTP_201_CREATED
        response_data["message"] = "Order has been created!"

        response_data["data"]['payment_type'] = "razor_pay"
        response_data["data"]["payment"] = payment
        response_data["data"]["order"] = self.serializer_class(order_obj).data

        return Response(response_data, status=response_data["status"])

    def update(self, request, pk):
        response_data = {"status": status.HTTP_400_BAD_REQUEST, "message": "", "data": {}}
        general = GeneralSetting.objects.all().first()

        if request.user.is_deleted:
            response_data["status"] = status.HTTP_404_NOT_FOUND
            response_data["message"] = "User has been deleted"
            return Response(response_data, status=response_data["status"])
        
        try:
            order_obj = self.get_queryset().get(id=pk)
            cancel_reason = request.data.get('cancel_reason', None)
        except Exception as e:
            response_data["message"] = f"{e}"
            return Response(response_data)

        if order_obj.order_status != 1:
            response_data["message"] = "You cannot cancel this order"
            return Response(response_data)

        order_obj.cancel_date = now()
        order_obj.cancel_reason = cancel_reason
        order_obj.order_status = 5
        order_obj.is_cancel = True

        if order_obj.is_paid:
            order_obj.refund_amount = order_obj.order_final_cost
            request.user.wallet = request.user.wallet + order_obj.order_final_cost
            message = f"Order has been cancelled. Amount {general.currency_symbol} {order_obj.order_final_cost} has been credited to wallet."
            Transaction().booking(request, order_obj.id, 2, message, order_obj.order_final_cost)
            request.user.save()

        response_data['status'] = status.HTTP_202_ACCEPTED
        response_data['message'] = "Order has been cancelled"
        order_obj.save()

        return Response(response_data)
