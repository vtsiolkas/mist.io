define('app/views/image_list', [
    'app/views/mistscreen',
    'text!app/templates/image_list.html','ember'],
    /**
     *
     * Images page
     *
     * @returns Class
     */
    function(MistScreen, image_list_html) {
        return MistScreen.extend({
	        template: Ember.Handlebars.compile(image_list_html),
	        init: function(){
	        	this._super();
	        	var that=this;
	            Ember.run.next(function(){
					$('.ui-input-search input').on('keypress', that.filterImages);
					//add this $('ul#images-list li.node:visible').length	        
	            });
	            $(window).on('scroll', this.didScroll);	

			},
	       
            willDestroyElement: function(){
            	$(window).off('scroll');
            	//$('.ui-input-search input').off('keypress');
   			},
   			
						
			// this is called every time we scroll
			didScroll: function(){
			    if (Mist.isScrolledToBottom()) {
			  	    var items = Mist.backendsController.content;
			  	   
			  	   //We loop through the images of every backend and append 20 at a time 
			  	   var counter = 0;
			  	    for (var i = 0; i < items.length; i++) {
			  	    	for (var j = 0; j < items[i].images.content.length; j ++) {
			  	    		if(!(items[i].images.content[j].star) &&  Mist.renderedImages.content.indexOf(items[i].images.content[j]) == -1 && counter < 20) {
			  	    			Mist.renderedImages.pushObject(items[i].images.content[j]);
			  	    			counter++;

			  	    		}
			  	    	}
			  	    }
			    }
			},
			
			isScrolledToBottom: function() {
    			var distanceToTop = $(document).height() - $(window).height(),
     		    top = $(document).scrollTop();

    			return top === distanceToTop;
  			},
  			
  			filterImages: function(){
  				if ($('ul#images-list li.node:visible').length < 5){
  					warn(7);
  				}else {
  					warn(3);
  				}
  				
  			}
           
        });
    }
);
