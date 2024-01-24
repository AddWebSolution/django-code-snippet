The provided code in views.py includes a viewset for user orders, offering functionality for:
-> Retrieving a list of previous orders for the user.
-> Displaying details of a specific order from the list.
-> Allowing the user to create a new order.
-> Enabling the user to cancel an existing order.


The UserOrderViewset class encompasses various overridden methods to achieve the desired outcomes:
-> get_serializer_class(): This method utilizes the AdvanceOrderSerializer for order details, while employing the OrderSerializer for other operations.
-> get_queryset(): This method filters the user's list of orders and removes unwanted entries.
-> create(): The logic within this method facilitates the creation of a new order, incorporating necessary validations and error handling.
-> update(): This method is employed for order cancellation, ensuring required updates and validations are carried out.
